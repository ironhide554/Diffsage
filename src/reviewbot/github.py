"""Minimal GitHub App client (JWT auth, installation tokens, PR review endpoints)."""

from __future__ import annotations

import base64
import os
import time

import httpx
import jwt

API = "https://api.github.com"


def _private_key() -> str:
    path = os.environ.get("GITHUB_PRIVATE_KEY_PATH")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    raw = os.environ.get("GITHUB_PRIVATE_KEY", "")
    return raw.replace("\\n", "\n")


def _app_jwt() -> str:
    app_id = os.environ["GITHUB_APP_ID"]
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": app_id}
    return jwt.encode(payload, _private_key(), algorithm="RS256")


class GitHubClient:
    def __init__(self, token: str) -> None:
        self._http = httpx.Client(
            base_url=API,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    @classmethod
    def for_installation(cls, installation_id: int) -> "GitHubClient":
        resp = httpx.post(
            f"{API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {_app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return cls(resp.json()["token"])

    def get_pull(self, repo: str, number: int) -> dict:
        resp = self._http.get(f"/repos/{repo}/pulls/{number}")
        resp.raise_for_status()
        return resp.json()

    def list_pr_files(self, repo: str, number: int) -> list[dict]:
        files: list[dict] = []
        page = 1
        while True:
            resp = self._http.get(
                f"/repos/{repo}/pulls/{number}/files", params={"per_page": 100, "page": page}
            )
            resp.raise_for_status()
            batch = resp.json()
            files += batch
            if len(batch) < 100:
                return files
            page += 1

    def get_file_text(self, repo: str, path: str, ref: str) -> str | None:
        resp = self._http.get(f"/repos/{repo}/contents/{path}", params={"ref": ref})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None

    def list_review_comment_bodies(self, repo: str, number: int) -> list[str]:
        bodies: list[str] = []
        page = 1
        while True:
            resp = self._http.get(
                f"/repos/{repo}/pulls/{number}/comments", params={"per_page": 100, "page": page}
            )
            resp.raise_for_status()
            batch = resp.json()
            bodies += [c.get("body", "") for c in batch]
            if len(batch) < 100:
                return bodies
            page += 1

    def list_review_bodies(self, repo: str, number: int) -> list[str]:
        bodies: list[str] = []
        page = 1
        while True:
            resp = self._http.get(
                f"/repos/{repo}/pulls/{number}/reviews", params={"per_page": 100, "page": page}
            )
            resp.raise_for_status()
            batch = resp.json()
            bodies += [r.get("body") or "" for r in batch]
            if len(batch) < 100:
                return bodies
            page += 1

    def create_review(self, repo: str, number: int, commit_sha: str, body: str, comments: list[dict]) -> None:
        resp = self._http.post(
            f"/repos/{repo}/pulls/{number}/reviews",
            json={"commit_id": commit_sha, "event": "COMMENT", "body": body, "comments": comments},
        )
        resp.raise_for_status()

    def post_issue_comment(self, repo: str, number: int, body: str) -> None:
        resp = self._http.post(f"/repos/{repo}/issues/{number}/comments", json={"body": body})
        resp.raise_for_status()
