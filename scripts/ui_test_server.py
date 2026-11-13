"""Isolated UI verification server. Replays a recorded public report; no RIPE calls."""

import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
# This executable test harness adds the uninstalled backend package to sys.path.
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.sources import UnknownRegid  # noqa: E402


class Replay:
    async def run(self, regid, emit):
        for i, stage in enumerate(
            ("resolve", "relations", "registrations", "organisations", "routing", "asn", "analysis")
        ):
            await emit(
                {"stage": stage, "message": "UI verification: " + stage, "done": i, "total": 7}
            )
            await asyncio.sleep(0.18)
        if regid == "aa.fail":
            raise UnknownRegid("regID not found. Check the spelling.")
        path = ROOT / "backend/tests/fixtures/ru_ecode_public.json"
        if path.exists():
            value = json.loads(path.read_text())
        else:
            value = {
                "schema_version": 1,
                "regid": regid,
                "holder": "UI test",
                "started_at": "2026-11-13T00:00:00Z",
                "completed_at": "2026-11-13T00:00:01Z",
                "bgp_snapshot": "2026-11-13T00:00:00Z",
                "coverage": {
                    "complete": True,
                    "mode": "public",
                    "scope": "Test fixture",
                    "note": "Test data only",
                    "warnings": [],
                },
                "summary": {
                    "ipv4": 0,
                    "ipv6": 0,
                    "asn": 0,
                    "objects": 0,
                    "routes": 0,
                    "findings": 0,
                    "errors": 0,
                    "warnings": 0,
                    "affected_resources": 0,
                    "allocation_announcement_checks": 0,
                },
                "resources": [],
                "findings": [],
                "routes": [],
                "sources": [],
                "manual_checks": [],
            }
        value = copy.deepcopy(value)
        value["regid"] = regid
        if regid == "aa.empty":
            value["resources"] = []
            value["routes"] = []
            value["findings"] = []
            value["summary"] = {
                k: 0 if isinstance(v, int) else v for k, v in value["summary"].items()
            }
        if regid == "aa.partial":
            value["coverage"]["complete"] = False
            value["coverage"]["warnings"] = [
                {"code": "test_source_unavailable", "message": "Source temporarily unavailable"}
            ]
        if regid == "aa.asn-status":
            base = next(row for row in value["resources"] if row["family"] == "asn")
            value["resources"] = []
            for number, state, bgp in [
                (64496, "recently_announced", "none"),
                (64497, "inactive_under_three_months", "none"),
                (64498, "recently_announced", "exact"),
                (64499, "younger_than_three_months", "none"),
            ]:
                row = copy.deepcopy(base)
                row.update(
                    id=f"aut-num:AS{number}",
                    resource=f"AS{number}",
                    activity=state,
                    bgp_state=bgp,
                    parent_id=None,
                    children_count=0,
                    finding_count=0,
                    context_finding_count=0,
                    descendant_bad=0,
                    findings=[],
                    context_findings=[],
                )
                value["resources"].append(row)
            value["findings"] = []
            value["routes"] = []
            value["summary"].update(
                ipv4=0,
                ipv6=0,
                asn=4,
                objects=4,
                routes=0,
                findings=0,
                errors=0,
                warnings=0,
                affected_resources=0,
            )
        return value


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="arc-ui-test-") as folder:
        app = create_app(Settings(db_path=Path(folder) / "tests.sqlite3"), Replay())
        uvicorn.run(app, host="127.0.0.1", port=8091, log_level="warning")
