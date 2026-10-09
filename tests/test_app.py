import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from reviewbot import app as app_module

SECRET = "s3cret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature():
    body = b"{}"
    assert app_module.verify_signature(SECRET, body, sign(body))
    assert not app_module.verify_signature(SECRET, body, sign(body, "other"))
    assert not app_module.verify_signature(SECRET, body, "deadbeef")
    assert not app_module.verify_signature(SECRET, body, None)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    calls: list[tuple] = []
    monkeypatch.setattr(app_module, "review_pull_request", lambda *a: calls.append(a))
    return TestClient(app_module.app), calls


def post(client, payload, event="pull_request", signature=None):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "X-Hub-Signature-256": signature or sign(body)}
    return client.post("/webhook", content=body, headers=headers)


def pr_payload(action="opened", draft=False):
    return {
        "action": action,
        "pull_request": {"number": 7, "draft": draft},
        "installation": {"id": 1},
        "repository": {"full_name": "o/r"},
    }


def test_bad_signature_is_rejected(client):
    c, calls = client
    assert post(c, pr_payload(), signature="sha256=bad").status_code == 401
    assert calls == []


def test_opened_pr_is_queued(client):
    c, calls = client
    resp = post(c, pr_payload())
    assert resp.status_code == 200 and resp.json() == {"queued": "o/r#7"}
    assert calls == [(1, "o/r", 7)]


def test_drafts_other_events_and_actions_are_ignored(client):
    c, calls = client
    assert "ignored" in post(c, pr_payload(draft=True)).json()
    assert "ignored" in post(c, pr_payload(action="closed")).json()
    assert "ignored" in post(c, {}, event="push").json()
    assert calls == []


def test_healthz(client):
    assert client[0].get("/healthz").json() == {"ok": True}
