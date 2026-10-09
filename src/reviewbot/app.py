"""FastAPI webhook server. Run with: uvicorn reviewbot.app:app"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .service import review_pull_request

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("reviewbot")

app = FastAPI(title="reviewbot")

HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/webhook")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    raw = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret or not verify_signature(secret, raw, x_hub_signature_256):
        log.warning("rejected webhook with invalid signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    if x_github_event != "pull_request":
        log.info("ignoring %s event", x_github_event)
        return {"ignored": x_github_event}

    payload = await request.json()
    pr = payload.get("pull_request", {})
    if payload.get("action") not in HANDLED_ACTIONS or pr.get("draft"):
        return {"ignored": payload.get("action")}

    installation_id = payload["installation"]["id"]
    repo = payload["repository"]["full_name"]
    number = pr["number"]

    # Respond to GitHub immediately (it times out at ~10s); review in the background.
    background.add_task(_run, installation_id, repo, number)
    return {"queued": f"{repo}#{number}"}


def _run(installation_id: int, repo: str, number: int) -> None:
    try:
        review_pull_request(installation_id, repo, number)
    except Exception:
        log.exception("review failed for %s#%s", repo, number)
