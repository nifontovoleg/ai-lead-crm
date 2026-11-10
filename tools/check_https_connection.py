"""Check public tunnel and auth without invoking AI or Telegram."""
import json
import argparse
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-enabled", action="store_true")
    args = parser.parse_args()
    cfg = Settings.load()
    url = (cfg.data_dir / "runtime/public_url.txt").read_text(encoding="utf-8-sig").strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".trycloudflare.com") or parsed.query or parsed.username or parsed.password:
        raise ValueError("Unexpected tunnel URL")
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        health = client.get(url + "/health")
        health.raise_for_status()
        assert health.json().get("service") == "ai-lead-crm"
        assert health.json().get("processing_enabled") is args.expect_enabled
        unauthorized = client.get(url + "/connection-check")
        assert unauthorized.status_code == 401
        connection = client.get(url + "/connection-check", headers={"X-CRM-Token": cfg.trigger_token})
        connection.raise_for_status()
        result = {"public_url": url, "health_http": health.status_code, "unauthorized_http": unauthorized.status_code,
                  "connection_http": connection.status_code, "connection": connection.json()}
        assert result["connection"]["ai_calls"] == result["connection"]["telegram_calls"] == 0
    (cfg.data_dir / "runtime/https-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("HTTPS check not completed:", type(exc).__name__)
        raise SystemExit(1) from None
