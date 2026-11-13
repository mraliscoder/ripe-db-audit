import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from app.config import Settings
from app.main import Jobs
from app.sources import RipeClient, SourceError
from app.storage import Store


def test_no_entries_404_is_empty_but_unknown_404_is_failure():
    async def run():
        client = RipeClient()
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    404,
                    json={
                        "errormessages": {"errormessage": [{"text": "ERROR:101: no entries found"}]}
                    },
                )
            )
        )
        assert await client.get("https://rest.db.ripe.net/search.json", empty_ok=True) == {
            "objects": {"object": []}
        }
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(404, json={"error": "wrong endpoint"})
            )
        )
        with pytest.raises(SourceError):
            await client.get("https://rest.db.ripe.net/search.json", empty_ok=True)
        await client.close()

    asyncio.run(run())


def test_portal_rejects_wrong_regid_and_never_places_key_in_url():
    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"regid": "nl.other", "ipv4Allocations": []})

        client = RipeClient()
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with pytest.raises(SourceError, match="different regID"):
            await client.portal("ru.ecode", "SECRET-PORTAL-KEY")
        assert "SECRET" not in str(requests[0].url)
        assert requests[0].headers["ncc-api-authorization"] == "SECRET-PORTAL-KEY"
        await client.close()

    asyncio.run(run())


def test_webhook_is_signed_and_has_stable_deduplication_id(tmp_path):
    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200)

        store = Store(tmp_path / "callback.db")
        store.create("job-one", "ru.ecode", "cache")
        store.finish("job-one", {"summary": {"objects": 5}}, 3600)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        settings = Settings(
            webhook_url="https://receiver.example/audit", webhook_secret="SIGNING-SECRET"
        )
        jobs = Jobs(store, SimpleNamespace(client=client), settings)
        await jobs.webhook("job-one")
        request = requests[0]
        expected = hmac.new(b"SIGNING-SECRET", request.content, hashlib.sha256).hexdigest()
        assert request.headers["X-ARC-Signature"] == "sha256=" + expected
        assert request.headers["X-ARC-Event-ID"] == "job-one"
        payload = json.loads(request.content)
        assert payload["event_id"] == "job-one" and payload["summary"] == {"objects": 5}
        assert "SIGNING-SECRET" not in request.content.decode()
        await client.aclose()
        store.close()

    asyncio.run(run())
