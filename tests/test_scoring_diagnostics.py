import json
from types import SimpleNamespace

import pytest

from crm.config import Settings, ROOT
from crm.models import Lead
from crm.scoring import ProxyScorer, ScoringFailure


@pytest.fixture
def lead():
    rows = json.loads((ROOT / "examples/leads.json").read_text(encoding="utf-8"))
    return Lead.from_row(rows[0])


def response(content, reason="stop", empty_choices=False):
    return SimpleNamespace(
        choices=[] if empty_choices else [SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=1600, total_tokens=1700,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=1500)),
    )


@pytest.mark.parametrize("reply,code", [
    (response("", "length"), "AI_INCOMPLETE"),
    (response(None), "AI_INCOMPLETE"),
    (response("not json"), "AI_INVALID_RESPONSE"),
    (response(None, empty_choices=True), "AI_EMPTY_CHOICES"),
])
def test_failed_response_is_preserved_without_retry(tmp_path, lead, reply, code):
    scorer = ProxyScorer(Settings(api_key="test-only", data_dir=tmp_path))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return reply

    scorer.client.chat.completions.create = create
    with pytest.raises(ScoringFailure) as caught:
        scorer.score(lead)
    assert caught.value.code == code
    assert len(calls) == 1
    record = json.loads(scorer.audit_path.read_text(encoding="utf-8"))
    assert record["code"] == code
    assert record["usage"]["reasoning_tokens"] == 1500
    assert "name" not in record["features"] and "id" not in record["features"]


def test_audit_redacts_configured_secrets(tmp_path, lead):
    secret = 'test-key-"quoted"'
    scorer = ProxyScorer(Settings(api_key=secret, telegram_token="test-bot-token", data_dir=tmp_path))
    scorer._audit(lead, response(secret + " test-bot-token"), "AI_INVALID_RESPONSE")
    record = json.loads(scorer.audit_path.read_text(encoding="utf-8"))
    assert record["content"] == "[REDACTED] [REDACTED]"


def test_model_score_and_explanation_are_preserved(tmp_path, lead):
    scorer = ProxyScorer(Settings(api_key="test-only", data_dir=tmp_path))
    raw = {"score": 99, "explanation": "Высокий приоритет по признакам лида.", "recommendation": "Назначить встречу с клиентом."}

    def create(**kwargs):
        assert json.loads(kwargs["messages"][1]["content"]) == lead.features()
        return response(json.dumps(raw))

    scorer.client.chat.completions.create = create
    assert scorer.score(lead).model_dump() == raw
    record = json.loads(scorer.audit_path.read_text(encoding="utf-8"))
    assert record["code"] == "OK" and record["accepted_score"] == 99
    assert record["explanation_source"] == "model"


def test_request_failure_is_logged_once_without_exception_text(tmp_path, lead):
    secret = "test-secret-in-error"
    scorer = ProxyScorer(Settings(api_key=secret, data_dir=tmp_path))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        try:
            raise TimeoutError(secret)
        except TimeoutError as cause:
            raise RuntimeError("Request to secret URL: " + secret) from cause

    scorer.client.chat.completions.create = create
    with pytest.raises(RuntimeError):
        scorer.score(lead)
    assert len(calls) == 1
    text = scorer.audit_path.read_text(encoding="utf-8")
    assert secret not in text and "secret URL" not in text
    record = json.loads(text)
    assert record["code"] == "REQUEST_FAILED:RuntimeError"
    assert record["cause_types"] == ["RuntimeError", "TimeoutError"]
    assert record["content"] is None and record["http_status"] is None


def test_pipeline_saves_safe_diagnostic_code_and_stops(tmp_path):
    from crm.engine import run_pipeline
    from crm.notifications import DeliveryLedger, DemoNotifier
    from crm.scoring import DemoScorer
    from crm.storage import LocalStore

    class Failure(DemoScorer):
        def score(self, lead):
            raise ScoringFailure("AI_INCOMPLETE")

    store = LocalStore(tmp_path / "leads.json", ROOT / "examples/leads.json")
    result = run_pipeline(Settings(data_dir=tmp_path), store, Failure(), DemoNotifier(tmp_path / "messages.txt"), DeliveryLedger(tmp_path / "state.sqlite"))
    assert result.errors == 1 and result.processed == 0 and result.notifications == 0
    assert store.rows()[0]["Ошибка"] == "SCORING_FAILED:AI_INCOMPLETE"
