"""Read-only ProxyAPI diagnostics: no model inference, no secret output."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from time import perf_counter

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm.config import Settings


def check(cfg, name, url, authenticated):
    events = []
    started = perf_counter()
    result = {"check": name}

    def trace(event, info):
        # Only event names; info can contain request headers with secrets.
        events.append(event)

    headers = {"Authorization": "Bearer " + cfg.api_key} if authenticated else {}
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False) as client:
            response = client.get(url, headers=headers, extensions={"trace": trace})
        result["http_status"] = response.status_code
        if response.status_code == 200:
            body = response.json()
            if name == "authenticated_models":
                result["configured_model_listed"] = any(row.get("id") == cfg.model for row in body.get("data", []))
            elif name == "authenticated_balance":
                balance = body.get("balance")
                if isinstance(balance, (int, float)):
                    result["balance_positive"] = balance > 0
    except Exception as exc:
        result["error"] = type(exc).__name__
    result["seconds"] = round(perf_counter() - started, 2)
    result["transport_events"] = events[-6:]
    return result


def main():
    cfg = Settings.load()
    if not cfg.api_key:
        print("ProxyAPI key is not configured")
        return 1
    checks = [
        ("unauthenticated_models", "https://api.proxyapi.ru/v1/models", False),
        ("authenticated_models", "https://api.proxyapi.ru/v1/models", True),
        ("authenticated_balance", "https://api.proxyapi.ru/proxyapi/balance", True),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(check, cfg, *item) for item in checks]
        results = [future.result() for future in futures]
    output = cfg.data_dir / "diagnostics/proxyapi_transport.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
