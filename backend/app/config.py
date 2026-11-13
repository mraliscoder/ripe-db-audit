import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .origins import normalize_origin

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("AUDIT_DB", ROOT / "data" / "audit.sqlite3"))
    )
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            value.strip()
            for value in os.environ.get("AUDIT_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        )
    )
    cache_seconds: int = 3600
    max_jobs: int = field(default_factory=lambda: int(os.environ.get("AUDIT_MAX_JOBS", "2")))
    max_objects: int = field(
        default_factory=lambda: int(os.environ.get("AUDIT_MAX_OBJECTS", "20000"))
    )
    max_routing_queries: int = field(
        default_factory=lambda: int(os.environ.get("AUDIT_MAX_ROUTING_QUERIES", "4096"))
    )
    job_timeout: int = field(
        default_factory=lambda: int(os.environ.get("AUDIT_JOB_TIMEOUT", "1800"))
    )
    webhook_url: str = field(default_factory=lambda: os.environ.get("AUDIT_WEBHOOK_URL", ""))
    webhook_secret: str = field(default_factory=lambda: os.environ.get("AUDIT_WEBHOOK_SECRET", ""))
    access_token: str = field(default_factory=lambda: os.environ.get("AUDIT_ACCESS_TOKEN", ""))
    portal_keys: dict = field(
        default_factory=lambda: json.loads(os.environ.get("RIPE_API_KEYS_JSON", "{}"))
    )

    def __post_init__(self):
        if any(normalize_origin(value) is None for value in self.allowed_origins):
            raise ValueError(
                "AUDIT_ALLOWED_ORIGINS must contain exact http(s) origins without paths"
            )
        if min(self.max_jobs, self.max_objects, self.max_routing_queries, self.job_timeout) <= 0:
            raise ValueError("Analysis limits must be positive")
        if self.portal_keys and not self.access_token:
            raise ValueError(
                "LIR Portal mode requires AUDIT_ACCESS_TOKEN to protect private resource inventories"
            )
        if self.webhook_url and (
            not self.webhook_url.startswith("https://") or not self.webhook_secret
        ):
            raise ValueError("Webhook requires HTTPS and AUDIT_WEBHOOK_SECRET")


RULESET = "arc-2026-11-v1"
