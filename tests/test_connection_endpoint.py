from types import SimpleNamespace

from fastapi.testclient import TestClient

from crm import api
from crm.config import Settings


def test_google_connection_check_does_not_call_processing(tmp_path, monkeypatch):
    credentials = tmp_path / "test-account.json"
    credentials.write_text("{}", encoding="utf-8")
    cfg = Settings(mode="live", credentials=credentials, spreadsheet_id="test-sheet", trigger_token="x" * 32)
    monkeypatch.setattr(api, "SheetsStore", lambda settings: SimpleNamespace(rows=lambda: [{"ID": "L001"}]))

    def forbidden(settings):
        raise AssertionError("Connection check must not invoke processing")

    monkeypatch.setattr(api, "process", forbidden)
    client = TestClient(api.create_app(cfg))
    assert client.get("/connection-check").status_code == 401
    result = client.get("/connection-check", headers={"X-CRM-Token": cfg.trigger_token})
    assert result.status_code == 200
    assert result.json() == {"status": "ok", "mode": "live", "lead_count": 1,
                             "processing_enabled": False, "ai_calls": 0, "telegram_calls": 0}
