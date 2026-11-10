import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

from crm.api import create_app
from crm.config import Settings, ROOT
from crm.engine import run_pipeline
from crm.models import Lead, Score
from crm.notifications import DeliveryLedger, DemoNotifier
from crm.scoring import DemoScorer, rubric_score, ProxyScorer
from crm.storage import LocalStore, atomic_json, ConcurrentEditError
from crm.analytics import summarize, render_report


@pytest.fixture
def setup(tmp_path):
    cfg = Settings(data_dir=tmp_path, batch_size=20)
    store = LocalStore(tmp_path / "leads.json", ROOT / "examples/leads.json")
    scorer = DemoScorer()
    notifier = DemoNotifier(tmp_path / "notifications.txt")
    ledger = DeliveryLedger(tmp_path / "state.sqlite")
    return cfg, store, scorer, notifier, ledger


def test_full_demo_and_repeat_do_not_duplicate(setup):
    cfg, store, scorer, notifier, ledger = setup
    first = run_pipeline(*setup)
    assert (first.processed, first.notifications, first.errors) == (5, 2, 0)
    before = notifier.path.read_text(encoding="utf-8")
    second = run_pipeline(*setup)
    assert second.processed == second.notifications == 0
    assert second.skipped == 6
    assert notifier.path.read_text(encoding="utf-8") == before
    stats = summarize(store.rows(), 70, scorer.version)
    assert stats["tiers"] == {"Горячий": 2, "Тёплый": 1, "Холодный": 2}
    assert stats["pipeline_budget"] == 210000
    assert stats["won"] == 1


def test_changed_features_rescore_only_changed_lead(setup):
    run_pipeline(*setup)
    rows = setup[1].rows()
    rows[3]["Бюджет"] = 120000
    atomic_json(setup[1].path, rows)
    result = run_pipeline(*setup)
    assert result.processed == 1


def test_invalid_duplicate_and_missing_ids_are_not_scored(setup):
    rows = setup[1].rows()
    rows[0]["ID"] = rows[1]["ID"]
    rows[2]["ID"] = ""
    rows[3]["Бюджет"] = -1
    atomic_json(setup[1].path, rows)
    result = run_pipeline(*setup)
    assert result.invalid == 4
    assert result.processed == 1
    assert result.notifications == 0


def test_batch_limit(setup):
    cfg, *deps = setup
    result = run_pipeline(replace(cfg, batch_size=2), *deps)
    assert result.processed == 2
    assert result.deferred == 3


def test_provider_failure_stops_batch_and_is_not_retried(setup):
    class Failure(DemoScorer):
        calls = 0

        def score(self, lead):
            self.calls += 1
            raise RuntimeError("SECRET_SHOULD_NOT_APPEAR")

    cfg, store, _, notifier, ledger = setup
    failure = Failure()
    first = run_pipeline(cfg, store, failure, notifier, ledger)
    assert first.errors == 1
    assert failure.calls == 1
    assert "SECRET_SHOULD_NOT_APPEAR" not in store.path.read_text(encoding="utf-8")
    assert store.rows()[0]["Ошибка"] == "SCORING_FAILED:RuntimeError"
    # Ранее не обработанные лиды могут обрабатываться следующим ручным запуском.
    # Первый ошибочный лид повторно не отправляется модели.
    rows = store.rows()
    for row in rows[1:]:
        row["Статус"] = "Проигран"
    atomic_json(store.path, rows)
    run_pipeline(cfg, store, failure, notifier, ledger)
    assert failure.calls == 1


def test_ambiguous_delivery_is_not_repeated(setup):
    class FailingNotifier:
        transport = "test"
        calls = 0

        def send(self, text):
            self.calls += 1
            raise TimeoutError("delivery unknown")

    cfg, store, scorer, _, ledger = setup
    notifier = FailingNotifier()
    run_pipeline(cfg, store, scorer, notifier, ledger)
    assert notifier.calls == 2
    run_pipeline(cfg, store, scorer, notifier, ledger)
    assert notifier.calls == 2
    assert store.rows()[0]["Уведомление"] == "Проверить доставку вручную"


def test_score_cache_survives_output_write_failure(setup):
    cfg, store, scorer, notifier, ledger = setup

    class CounterScorer(DemoScorer):
        calls = 0

        def score(self, lead):
            self.calls += 1
            return super().score(lead)

    counter = CounterScorer()
    original = store.update

    def fail(original_row, changes):
        raise OSError("Sheets write unavailable")

    store.update = fail
    with pytest.raises(OSError):
        run_pipeline(cfg, store, counter, notifier, ledger)
    store.update = original
    run_pipeline(cfg, store, counter, notifier, ledger)
    assert counter.calls == 5


def test_ledger_prevents_duplicate_when_sheet_update_after_send_fails(setup):
    cfg, store, scorer, notifier, ledger = setup
    original = store.update

    def fail(original_row, changes):
        if changes.get("Уведомление") == "Записано в файл":
            raise OSError("write failed after send")
        return original(original_row, changes)

    store.update = fail
    with pytest.raises(OSError):
        run_pipeline(*setup)
    store.update = original
    run_pipeline(*setup)
    text = notifier.path.read_text(encoding="utf-8")
    assert text.count("Горячий лид L001") == 1


def test_concurrent_edit_rejects_result(setup):
    store = setup[1]
    row = store.rows()[0]
    rows = store.rows()
    rows[0]["Бюджет"] = 1
    atomic_json(store.path, rows)
    with pytest.raises(ConcurrentEditError):
        store.update(row, {"AI балл": 100})


def test_analytics_excludes_stale_scores(setup):
    run_pipeline(*setup)
    rows = setup[1].rows()
    rows[0]["Бюджет"] = 0
    stats = summarize(rows, 70, setup[2].version)
    assert stats["tiers"]["Горячий"] == 1
    assert stats["unscored_active"] == 1


@pytest.mark.parametrize("payload", [
    {"score": 101, "explanation": "Достаточно длинное объяснение", "recommendation": "Уточнить задачу клиента"},
    {"score": True, "explanation": "Достаточно длинное объяснение", "recommendation": "Уточнить задачу клиента"},
    {"score": "90", "explanation": "Достаточно длинное объяснение", "recommendation": "Уточнить задачу клиента"},
    {"score": 90, "explanation": "", "recommendation": "Уточнить задачу клиента"},
])
def test_invalid_model_responses_rejected(payload):
    with pytest.raises(ValueError):
        Score.model_validate_json(json.dumps(payload))


def test_proxy_request_contract_without_network(setup):
    from types import SimpleNamespace
    scorer = ProxyScorer(replace(setup[0], api_key="test-only"))
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({"score": 100, "explanation": "Рекомендация, достаточный бюджет и высокая активность.", "recommendation": "Назначить встречу и согласовать техническое задание."})))])

    scorer.client.chat.completions.create = create
    lead = Lead.from_row(setup[1].rows()[0])
    assert scorer.score(lead).score == 100
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["max_completion_tokens"] == 1600
    assert lead.name not in captured["messages"][1]["content"]


def test_targeted_cached_run_does_not_score_or_send(setup):
    run_pipeline(*setup)
    cfg, store, scorer, notifier, ledger = setup

    def blocked(*args, **kwargs):
        raise AssertionError("External calls disabled for cached verification")

    scorer.score = blocked
    notifier.send = blocked
    result = run_pipeline(*setup, lead_id="L001", allow_scoring=False)
    assert result.processed == result.errors == result.notifications == 0
    assert result.skipped == 1
    assert store.rows()[1]["AI балл"] == 81


def test_html_escapes_client_text(setup):
    rows = setup[1].rows()
    rows[0]["Имя"] = '<script>alert(1)</script>'
    html = render_report(rows, summarize(rows, 70, setup[2].version), "demo")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_http_auth_health_and_processing(setup):
    cfg = replace(setup[0], trigger_token="x" * 32)
    client = TestClient(create_app(cfg))
    assert client.get("/health").json()["mode"] == "demo"
    assert client.post("/process").status_code == 401
    assert client.post("/process", headers={"X-CRM-Token": "wrong"}).status_code == 401
    response = client.post("/process", headers={"X-CRM-Token": cfg.trigger_token})
    assert response.status_code == 200
    assert response.json()["processed"] == 5
    receipt = json.loads((cfg.data_dir / "runtime/last_http_process.json").read_text(encoding="utf-8"))
    assert receipt["caller"] == "other" and receipt["result"]["processed"] == 5
    with FileLock(str(cfg.data_dir / "processing.lock")):
        assert client.post("/process", headers={"X-CRM-Token": cfg.trigger_token}).status_code == 409


def test_google_request_receipt_does_not_store_headers(setup):
    cfg = replace(setup[0], trigger_token="test-token-" + "x" * 32)
    client = TestClient(create_app(cfg))
    response = client.post("/process", headers={"X-CRM-Token": cfg.trigger_token,
                          "User-Agent": "Google-Apps-Script; id: do-not-store-this-id"})
    assert response.status_code == 200
    raw = (cfg.data_dir / "runtime/last_http_process.json").read_text(encoding="utf-8")
    receipt = json.loads(raw)
    assert receipt["caller"] == "google-apps-script"
    assert cfg.trigger_token not in raw and "do-not-store-this-id" not in raw


def test_missing_token_disables_endpoint(setup):
    client = TestClient(create_app(setup[0]))
    assert client.post("/process").status_code == 503


def test_live_requires_explicit_configuration():
    with pytest.raises(ValueError, match="Требуется настройка"):
        Settings(mode="live").require_live(processing=True)
