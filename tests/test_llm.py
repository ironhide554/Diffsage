import pytest
from google.genai import errors

from reviewbot import llm as llm_module
from reviewbot.models import Verdict, Verdicts


class FakeResponse:
    def __init__(self, parsed=None, text=""):
        self.parsed, self.text = parsed, text


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, **_):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def make(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda *_: None)

    def _make(outcomes):
        models = FakeModels(outcomes)
        fake_client = type("C", (), {"models": models})()
        monkeypatch.setattr(llm_module.genai, "Client", lambda **_: fake_client)
        return llm_module.GeminiClient(api_key="k", rpm=6000), models

    return _make


GOOD = Verdicts(verdicts=[Verdict(index=0, valid=True, reason="ok")])


def api_error(code):
    return errors.APIError(code, {"error": {"message": "x"}})


def test_returns_parsed_object(make):
    client, _ = make([FakeResponse(parsed=GOOD)])
    assert client.generate("s", "p", Verdicts) == GOOD


def test_retries_on_429_then_succeeds(make):
    client, models = make([api_error(429), FakeResponse(parsed=GOOD)])
    assert client.generate("s", "p", Verdicts) == GOOD
    assert models.calls == 2


def test_non_retryable_error_propagates(make):
    client, _ = make([api_error(400)])
    with pytest.raises(errors.APIError):
        client.generate("s", "p", Verdicts)


def test_parse_failures_return_none_after_retries(make):
    client, models = make([FakeResponse(text="not json")] * 5)
    assert client.generate("s", "p", Verdicts) is None
    assert models.calls == 5


def test_falls_back_to_text_json(make):
    client, _ = make([FakeResponse(parsed=None, text=GOOD.model_dump_json())])
    assert client.generate("s", "p", Verdicts) == GOOD


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm_module.GeminiClient()


def test_daily_quota_fails_fast_without_retrying(make):
    err = errors.APIError(
        429,
        {"error": {"message": "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}},
    )
    client, models = make([err, FakeResponse(parsed=GOOD)])
    with pytest.raises(llm_module.QuotaExceeded):
        client.generate("s", "p", Verdicts)
    assert models.calls == 1
