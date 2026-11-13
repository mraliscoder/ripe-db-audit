import asyncio
import time

import pytest
from app.config import Settings
from app.main import create_app
from app.sources import UnknownRegid, catalog_entry
from fastapi.testclient import TestClient


class FakeAnalyzer:
    def __init__(self, *, partial=False, fail=False):
        self.calls = 0
        self.partial = partial
        self.fail = fail

    async def run(self, regid, emit):
        self.calls += 1
        await emit({"stage": "registrations", "message": "Testing progress", "done": 1, "total": 2})
        await asyncio.sleep(0.15)
        if self.fail:
            raise UnknownRegid("regID not found")
        await emit({"stage": "analysis", "message": "Complete", "done": 1, "total": 1})
        return {
            "regid": regid,
            "coverage": {"complete": not self.partial},
            "summary": {"objects": 0},
            "resources": [],
            "routes": [],
            "findings": [],
            "sources": [],
        }


def wait(client, job):
    for _ in range(100):
        value = client.get("/api/analyses/" + job).json()
        if value["status"] in ("complete", "partial", "failed"):
            return value
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


def test_regid_catalog_does_not_leak_adjacent_account_prefixes():
    entry = catalog_entry(
        "aa.first\n    First\n    20200101 192.0.2.0/24 ALLOCATED PA\naa.second\n    Second\n    20200101 2001:db8::/32\n",
        "aa.first",
    )
    assert entry["prefixes"] == ["192.0.2.0/24"]
    with pytest.raises(UnknownRegid):
        catalog_entry("aa.first\n First\n", "aa.nope")


def test_concurrent_duplicate_requests_share_job_and_complete_cache(tmp_path):
    analyzer = FakeAnalyzer()
    with TestClient(create_app(Settings(db_path=tmp_path / "test.db"), analyzer)) as client:
        a = client.post("/api/analyses", json={"regid": " RU.ECODE "}).json()
        b = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()
        assert a["id"] == b["id"]
        done = wait(client, a["id"])
        assert done["status"] == "complete"
        c = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()
        assert c["cache_hit"] and c["id"] == a["id"] and analyzer.calls == 1
        assert 3590 < done["expires"] - done["completed"] <= 3600


def test_sse_replays_and_last_event_id_resumes(tmp_path):
    with TestClient(create_app(Settings(db_path=tmp_path / "test.db"), FakeAnalyzer())) as client:
        job = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        wait(client, job)
        response = client.get("/api/analyses/" + job + "/events")
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "Testing progress" in response.text and "event: finished" in response.text
        resumed = client.get("/api/analyses/" + job + "/events", headers={"Last-Event-ID": "2"})
        assert "id: 1\n" not in resumed.text and "id: 2\n" not in resumed.text


@pytest.mark.parametrize("partial,fail", [(True, False), (False, True)])
def test_failed_or_partial_jobs_do_not_poison_success_cache(tmp_path, partial, fail):
    fake = FakeAnalyzer(partial=partial, fail=fail)
    with TestClient(create_app(Settings(db_path=tmp_path / "test.db"), fake)) as client:
        a = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        wait(client, a)
        b = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()
        assert b["id"] != a and not b["cache_hit"]


def test_expired_cache_recomputes_and_unknown_ids_are_404(tmp_path):
    settings = Settings(db_path=tmp_path / "test.db", cache_seconds=0)
    with TestClient(create_app(settings, FakeAnalyzer())) as client:
        a = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        wait(client, a)
        b = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        assert b != a
        assert client.get("/api/analyses/unknown").status_code == 404


def test_url_input_is_rejected_and_result_is_not_available_while_running(tmp_path):
    with TestClient(create_app(Settings(db_path=tmp_path / "test.db"), FakeAnalyzer())) as client:
        assert client.post("/api/analyses", json={"regid": "http://127.0.0.1/"}).status_code == 422
        job = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        assert client.get("/api/analyses/" + job + "/result").status_code == 409
        assert (
            client.post(
                "/api/analyses",
                json={"regid": "ru.ecode"},
                headers={"Origin": "https://other.example"},
            ).status_code
            == 403
        )


def test_existing_cached_findings_export_in_english(tmp_path):
    class LegacyAnalyzer(FakeAnalyzer):
        async def run(self, regid, emit):
            result = await super().run(regid, emit)
            result["findings"] = [
                {
                    "resource": "AS64496",
                    "family": "asn",
                    "org_handles": ["ORG-EAI4-RIPE"],
                    "message": "Нет анонсов более трёх месяцев.",
                    "message_en": "No announcements for more than three months.",
                    "code": "asn_inactive_over_three_months",
                }
            ]
            return result

    with TestClient(create_app(Settings(db_path=tmp_path / "test.db"), LegacyAnalyzer())) as client:
        job = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        wait(client, job)
        exported = client.get(f"/api/analyses/{job}/export.csv")
        assert exported.status_code == 200
        assert "No announcements for more than three months." in exported.text
        assert "Нет анонсов" not in exported.text
        found = client.get(f"/api/analyses/{job}/findings", params={"q": "No announcements"}).json()
        assert found["total"] == 1
