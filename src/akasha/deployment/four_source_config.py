from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PRODUCTION_SOURCES = {
    "sentinel-2-l2a": (
        "AKASHA_SENTINEL2_PRELOAD_SCHEDULE_ENABLED",
    ),
    "resourcesat-2a-liss3-boa": (
        "AKASHA_RESOURCESAT_LISS3_PRELOAD_SCHEDULE_ENABLED",
        "AKASHA_RESOURCESAT_LISS3_READINESS_ENABLED",
    ),
    "resourcesat-2a-liss4-mx70-l2": (
        "AKASHA_RESOURCESAT_LISS4_PRELOAD_SCHEDULE_ENABLED",
        "AKASHA_RESOURCESAT_LISS4_READINESS_ENABLED",
    ),
    "resourcesat-2a-awifs-boa": (
        "AKASHA_RESOURCESAT_AWIFS_PRELOAD_SCHEDULE_ENABLED",
        "AKASHA_RESOURCESAT_AWIFS_READINESS_ENABLED",
    ),
}
RUNTIME_SERVICES = {
    "api",
    "scheduler",
    "worker-search",
    "worker-download",
    "worker-process",
    "worker-heavy",
}


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    services = config.get("services") or {}
    for service_name in sorted(RUNTIME_SERVICES):
        service = services.get(service_name)
        if not isinstance(service, dict):
            errors.append(f"missing runtime service: {service_name}")
            continue
        environment = service.get("environment") or {}
        for source_id, flags in PRODUCTION_SOURCES.items():
            for flag in flags:
                if str(environment.get(flag, "")).lower() != "true":
                    errors.append(f"{service_name}: {source_id} requires {flag}=true")

    api_environment = (services.get("api") or {}).get("environment") or {}
    scheduler_environment = (services.get("scheduler") or {}).get("environment") or {}
    for key in ("AKASHA_API_KEY_HASHES", "AKASHA_BHOONIDHI_USER_ID", "AKASHA_BHOONIDHI_PASSWORD"):
        if not str(api_environment.get(key, "")).strip():
            errors.append(f"api: {key} must be configured")
    signing_secret = str(api_environment.get("AKASHA_SIGNING_SECRET", ""))
    if signing_secret == "change-me" or len(signing_secret) < 32:
        errors.append(
            "api: AKASHA_SIGNING_SECRET must be a non-default secret of at least 32 characters"
        )
    public_base_url = str(api_environment.get("AKASHA_PUBLIC_BASE_URL", ""))
    if not public_base_url.startswith(("http://", "https://")) or "localhost" in public_base_url:
        errors.append("api: AKASHA_PUBLIC_BASE_URL must be a non-local HTTP(S) URL")
    if str(scheduler_environment.get("AKASHA_BHOONIDHI_APPROVED_RUNTIME", "")).lower() != "true":
        errors.append("scheduler: AKASHA_BHOONIDHI_APPROVED_RUNTIME must be true")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_four_source_config.py <rendered-compose.json>", file=sys.stderr)
        return 2
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    errors = validate_config(config)
    if errors:
        print("Four-source deployment preflight failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Four-source deployment preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
