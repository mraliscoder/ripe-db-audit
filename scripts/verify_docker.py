"""Build and check Docker in an isolated Compose project; never query live RIPE."""

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    project = "ripe-db-audit-smoke-" + uuid.uuid4().hex[:10]
    env = {
        **os.environ,
        "AUDIT_IMAGE": "ripe-db-audit:local",
        "AUDIT_BIND": "127.0.0.1",
        "AUDIT_PORT": "0",
        "AUDIT_ACCESS_TOKEN": "",
        "AUDIT_ALLOWED_ORIGINS": "",
        "RIPE_API_KEYS_JSON": "{}",
        "AUDIT_WEBHOOK_URL": "",
        "AUDIT_WEBHOOK_SECRET": "",
    }
    compose = ["docker", "compose", "-p", project]
    checks = []

    def run(*arguments):
        return subprocess.run(
            arguments, cwd=ROOT, env=env, check=True, text=True, capture_output=True
        ).stdout.strip()

    def read(url):
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.status == 200
            return response.read().decode()

    def up():
        run(*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "60")
        address = run(*compose, "port", "analyzer", "8090").splitlines()[0]
        assert address.startswith("127.0.0.1:")
        return "http://" + address

    try:
        run(*compose, "config", "--quiet")
        if not args.skip_build:
            run(*compose, "build")
        base = up()
        assert json.loads(read(base + "/api/health"))["status"] == "ok"
        checks.append("Healthy API on an automatically assigned loopback port")
        for site, expected in [("same-origin", 422), ("cross-site", 403)]:
            request = urllib.request.Request(
                base + "/api/analyses",
                data=b'{"regid":"invalid"}',
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://audit.example",
                    "Sec-Fetch-Site": site,
                },
            )
            try:
                urllib.request.urlopen(request, timeout=10)
                raise AssertionError("Invalid regID unexpectedly accepted")
            except urllib.error.HTTPError as error:
                assert error.code == expected, (site, error.code)
        checks.append("HTTPS proxy origins accepted; cross-site browser writes rejected")

        html = read(base)
        assert "RIPE DB Audit" in html
        for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
            assert read(base + asset)
        checks.append("Production frontend and bundled assets served by API container")
        container = run(*compose, "ps", "-q", "analyzer")
        info = json.loads(run("docker", "inspect", container))[0]
        assert info["Config"]["User"] == "10001:10001"
        assert info["HostConfig"]["ReadonlyRootfs"]
        assert "ALL" in info["HostConfig"]["CapDrop"]
        assert info["State"]["Health"]["Status"] == "healthy"
        checks.append("Non-root runtime, read-only application files and healthcheck")
        job = "docker-persistence-" + uuid.uuid4().hex
        seed = f"""
from pathlib import Path
from backend.app.storage import Store
store = Store(Path('/app/data/audit.sqlite3'))
store.create({job!r}, 'aa.smoke', 'isolated-docker-check')
store.finish({job!r}, {{'regid': 'aa.smoke', 'coverage': {{'complete': True}},
'summary': {{'objects': 0}}, 'resources': [], 'routes': [], 'findings': [], 'sources': []}}, 0)
store.close()
"""
        run(*compose, "exec", "-T", "analyzer", "python", "-c", seed)
        before = json.loads(read(f"{base}/api/analyses/{job}/result"))
        assert before["job_id"] == job and before["cache_valid"] is False
        assert "event: finished" in read(f"{base}/api/analyses/{job}/events")
        checks.append("SQLite writable in data volume; expired snapshot and SSE still accessible")
        run(*compose, "down")
        base = up()
        after = json.loads(read(f"{base}/api/analyses/{job}/result"))
        assert before == after
        checks.append("Same job ID and result survive container removal and recreation")
        report = {"passed": True, "image": "ripe-db-audit:local", "checks": checks}
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts/docker-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or str(error)) from error
    finally:
        # Only the uniquely named test project and its disposable volume are removed.
        run(*compose, "down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
