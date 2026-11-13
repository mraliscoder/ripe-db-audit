import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from . import __version__
from .config import RULESET, Settings
from .engine import AnalysisLimit, Analyzer
from .models import regid
from .origins import allows_request
from .sources import RipeClient, SourceError, UnknownRegid
from .storage import Store

log = logging.getLogger("arc")
TERMINAL = {"complete", "partial", "failed", "interrupted"}


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regid: str


class LoginRequest(BaseModel):
    token: str


class Jobs:
    def __init__(self, store, client, settings, analyzer=None):
        self.store, self.client, self.settings = store, client, settings
        self.analyzer = analyzer or Analyzer(client, settings)
        self.lock, self.gate = asyncio.Lock(), asyncio.Semaphore(settings.max_jobs)
        self.active, self.tasks = {}, set()

    async def start(self, value):
        value = regid(value)
        # Credential changes cannot serve data from a different resource scope.
        scope = hashlib.sha256(self.settings.portal_keys.get(value, "public").encode()).hexdigest()
        key = value + ":" + RULESET + ":" + scope
        async with self.lock:
            cached = self.store.cache(key)
            if cached:
                return {**cached, "cache_hit": True}
            if value in self.active:
                return {**self.store.get(self.active[value]), "cache_hit": False, "joined": True}
            if len(self.active) >= 20:
                raise HTTPException(429, "The analysis queue is full. Please try again later.")
            job = uuid.uuid4().hex
            self.store.create(job, value, key)
            self.active[value] = job
            task = asyncio.create_task(self.run(job, value))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return {**self.store.get(job), "cache_hit": False}

    async def run(self, job, value):
        async def emit(event):
            self.store.event(job, event)

        try:
            async with self.gate:
                self.store.set_status(job, "running")
                async with asyncio.timeout(self.settings.job_timeout):
                    result = await self.analyzer.run(value, emit)
                partial = not result["coverage"]["complete"]
                self.store.finish(job, result, self.settings.cache_seconds, partial)
                await emit(
                    {
                        "stage": "complete",
                        "message": "Analysis completed with incomplete source data"
                        if partial
                        else "Analysis complete",
                        "done": 1,
                        "total": 1,
                        "summary": result["summary"],
                        "status": "partial" if partial else "complete",
                    }
                )
        except asyncio.CancelledError:
            self.store.fail(job, "Analysis stopped during server shutdown. Please run it again.")
            raise
        except (UnknownRegid, SourceError, AnalysisLimit) as error:
            self.store.fail(job, str(error))
            await emit({"stage": "failed", "message": str(error)})
        except TimeoutError:
            self.store.fail(
                job,
                "Analysis exceeded the time limit. Try again or increase the server limit.",
            )
            await emit({"stage": "failed", "message": "Analysis time limit exceeded"})
        except Exception:
            log.exception("Analysis failed for job %s", job)
            self.store.fail(
                job,
                "Analysis could not be completed. Data has not been marked as verified. Please try again.",
            )
            await emit({"stage": "failed", "message": "Analysis could not be completed"})
        finally:
            self.active.pop(value, None)
        if self.settings.webhook_url:
            await self.webhook(job)

    async def webhook(self, job):
        info = self.store.get(job, result=True)
        payload = {
            "event": "analysis.finished",
            "event_id": job,
            "job_id": job,
            "regid": info["regid"],
            "status": info["status"],
            "completed_at": info["completed"],
            "summary": (info["result"] or {}).get("summary"),
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(self.settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        for attempt in range(3):
            try:
                response = await self.client.client.post(
                    self.settings.webhook_url,
                    content=raw,
                    headers={
                        "Content-Type": "application/json",
                        "X-ARC-Event-ID": job,
                        "X-ARC-Signature": "sha256=" + signature,
                    },
                )
                response.raise_for_status()
                return
            except (httpx.HTTPError, ValueError):
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        # Delivery failure does not invalidate or cause recomputation of an audit.
        self.store.event(
            job,
            {"stage": "webhook", "message": "Could not deliver the webhook notification"},
        )

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)


def create_app(settings=None, analyzer=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store, client = Store(settings.db_path), RipeClient()
        app.state.jobs = Jobs(store, client, settings, analyzer)
        try:
            yield
        finally:
            await app.state.jobs.close()
            await client.close()
            store.close()

    app = FastAPI(title="RIPE DB Audit", version=__version__, lifespan=lifespan)

    def valid_session(cookie):
        try:
            expiry, nonce, signature = cookie.split(".")
            expected = hmac.new(
                settings.access_token.encode(), (expiry + "." + nonce).encode(), hashlib.sha256
            ).hexdigest()
            return int(expiry) > time.time() and hmac.compare_digest(expected, signature)
        except (ValueError, AttributeError, TypeError):
            return False

    @app.middleware("http")
    async def headers(request, call_next):
        # Browser jobs stay on this origin; no secret/token is accepted from URLs.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not allows_request(
            request, settings.allowed_origins
        ):
            return Response(
                json.dumps(
                    {
                        "detail": "Request origin is not allowed. Configure AUDIT_ALLOWED_ORIGINS for this site's public URL."
                    }
                ),
                status_code=403,
                media_type="application/json",
            )
        if (
            settings.access_token
            and request.url.path.startswith("/api/")
            and request.url.path not in ("/api/session", "/api/health")
        ):
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not (
                hmac.compare_digest(bearer.encode(), settings.access_token.encode())
                or valid_session(request.cookies.get("arc_session"))
            ):
                return Response(
                    '{"detail":"Sign in required"}', status_code=401, media_type="application/json"
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def manager():
        return app.state.jobs

    def get_job(job, result=False):
        info = manager().store.get(job, result)
        if not info:
            raise HTTPException(404, "Analysis not found")
        return info

    def get_result(job):
        info = get_job(job, True)
        if info["result"] is None:
            raise HTTPException(409, "Result is not ready yet")
        return info, info["result"]

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "ruleset": RULESET, "cache_seconds": settings.cache_seconds}

    @app.post("/api/session")
    async def session(body: LoginRequest, request: Request):
        if not settings.access_token or not hmac.compare_digest(
            body.token.encode(), settings.access_token.encode()
        ):
            raise HTTPException(401, "Invalid access token")
        payload = str(int(time.time()) + 8 * 3600) + "." + uuid.uuid4().hex
        signature = hmac.new(
            settings.access_token.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        response = Response('{"authenticated":true}', media_type="application/json")
        response.set_cookie(
            "arc_session",
            payload + "." + signature,
            max_age=8 * 3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @app.post("/api/analyses", status_code=202)
    async def start(body: StartRequest):
        try:
            return await manager().start(body.regid)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/analyses")
    async def recent():
        return {"items": manager().store.recent()}

    @app.get("/api/analyses/{job}")
    async def status(job: str):
        return get_job(job)

    @app.get("/api/analyses/{job}/events")
    async def events(job: str, request: Request, after: int = Query(0, ge=0)):
        get_job(job)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = after

        async def stream():
            nonlocal cursor
            last_heartbeat = time.monotonic()
            while not await request.is_disconnected():
                for seq, payload in manager().store.events(job, cursor):
                    yield f"id: {seq}\nevent: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    cursor = seq
                if get_job(job)["status"] in TERMINAL:
                    yield "event: finished\ndata: {}\n\n"
                    break
                if time.monotonic() - last_heartbeat >= 10:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(0.4)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/analyses/{job}/result")
    async def result(job: str, full: bool = False):
        info, result = get_result(job)
        public = (
            result
            if full
            else {k: v for k, v in result.items() if k not in ("resources", "routes", "findings")}
        )
        return {
            **public,
            "job_id": job,
            "status": info["status"],
            "expires_at": info["expires"],
            "cache_valid": info["cached"],
        }

    @app.get("/api/analyses/{job}/resources")
    async def resources(
        job: str,
        family: str = "",
        q: str = "",
        org: str = "",
        bad: bool = False,
        include_bgp: bool = False,
        parent: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        _, data = get_result(job)
        items = data["resources"]
        if not include_bgp:
            items = [r for r in items if r["type"] != "bgp"]
        if family:
            items = [r for r in items if r["family"] == family]
        if parent is not None and not q and not org and not bad:
            items = [r for r in items if (r["parent_id"] or "") == parent]
        if q:
            items = [
                r
                for r in items
                if all(
                    t
                    in " ".join(
                        [
                            r["resource"],
                            r["netname"],
                            *r["org_handles"],
                            *r["org_names"],
                            *r["origins"],
                        ]
                    ).lower()
                    for t in q.lower().split()
                )
            ]
        if org:
            items = [r for r in items if org in r["org_handles"]]
        if bad:
            items = [r for r in items if r["finding_count"] or r["context_finding_count"]]
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "offset": offset,
            "limit": limit,
        }

    @app.get("/api/analyses/{job}/resources/{resource_id:path}")
    async def resource(job: str, resource_id: str):
        _, data = get_result(job)
        row = next((r for r in data["resources"] if r["id"] == resource_id), None)
        if not row:
            raise HTTPException(404, "Resource not found")
        ids = set(row["findings"] + row.get("context_findings", []))
        return {
            **row,
            "finding_details": [f for f in data["findings"] if f["id"] in ids],
            "route_details": [
                r
                for r in data["routes"]
                if r["prefix"] in row["routes"] + row["more_routes"] + row["covering_routes"]
            ],
        }

    @app.get("/api/analyses/{job}/findings")
    async def findings(
        job: str,
        family: str = "",
        q: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        _, data = get_result(job)
        items = [
            f
            for f in data["findings"]
            if (not family or f["family"] == family)
            and q.lower()
            in " ".join(
                [f["resource"], f["code"], f["message"], f.get("message_en", ""), *f["org_handles"]]
            ).lower()
        ]
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "offset": offset,
            "limit": limit,
        }

    @app.get("/api/analyses/{job}/export.csv")
    async def export(job: str):
        _, data = get_result(job)
        out = io.StringIO(newline="")
        columns = [
            "family",
            "resource",
            "org_handles",
            "code",
            "severity",
            "basis",
            "message",
            "message_en",
            "announced_prefix",
            "origins",
            "source_url",
        ]
        writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in data["findings"]:
            clean = {k: ";".join(v) if isinstance(v, list) else v for k, v in row.items()}
            clean["message"] = clean.get("message_en") or clean.get("message", "")
            # Spreadsheet formula injection is not a property of a RIPE registration.
            clean = {
                k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                for k, v in clean.items()
            }
            writer.writerow(clean)
        return Response(
            "\ufeff" + out.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{data["regid"]}-findings.csv"'},
        )

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    async def index():
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return Response(
            "Build the frontend first: cd frontend && npm install && npm run build",
            status_code=503,
        )

    return app


app = create_app()
