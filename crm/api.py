import hmac
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from filelock import Timeout

from .config import Settings
from .runtime import process
from .storage import SheetsStore, atomic_json


def create_app(settings=None):
    cfg = settings or Settings.load()
    app = FastAPI(title="CRM trigger", docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(token):
        if len(cfg.trigger_token) < 32:
            raise HTTPException(503, "CRM_TRIGGER_TOKEN is not configured")
        if not hmac.compare_digest(token.encode(), cfg.trigger_token.encode()):
            raise HTTPException(401, "Unauthorized")

    @app.get("/health")
    def health():
        # Это liveness; не проверка доступности внешних провайдеров.
        return {"status": "ok", "mode": cfg.mode, "service": "ai-lead-crm", "processing_enabled": cfg.live_enabled}

    @app.get("/connection-check")
    def connection_check(x_crm_token: str = Header(default="")):
        authorize(x_crm_token)
        try:
            cfg.require_live()
            rows = SheetsStore(cfg).rows()
            return {"status": "ok", "mode": cfg.mode, "lead_count": len(rows),
                    "processing_enabled": cfg.live_enabled, "ai_calls": 0, "telegram_calls": 0}
        except Exception:
            raise HTTPException(503, "Google Sheets connection check failed") from None

    @app.post("/process")
    def trigger(request: Request, x_crm_token: str = Header(default="")):
        authorize(x_crm_token)
        try:
            result = process(cfg).model_dump()
            # Classify the caller without saving headers, IPs, script IDs or secrets.
            caller = "google-apps-script" if "google-apps-script" in request.headers.get("user-agent", "").lower() else "other"
            atomic_json(cfg.data_dir / "runtime/last_http_process.json", {
                "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "caller": caller, "result": result,
            })
            return result
        except Timeout:
            raise HTTPException(409, "Processing is already running") from None
        except Exception:
            raise HTTPException(503, "Processing failed; inspect configuration and sheet error columns") from None

    return app


app = create_app()
