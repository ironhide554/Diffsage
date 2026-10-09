"""Glue: review one pull request end to end and post the result."""

from __future__ import annotations

import logging
import threading

import httpx

from .config import parse_config
from .github import GitHubClient
from .llm import GeminiClient
from .models import Finding
from .reviewer import (
    SUMMARY_MARKER,
    existing_fingerprints,
    fingerprint,
    rank_and_cap,
    render_comment,
    render_summary,
    review_file,
    skip_reason,
    summarize,
)

log = logging.getLogger("reviewbot")

# One review per PR at a time. A push that lands mid-review is remembered and
# triggers exactly one follow-up run, instead of racing and double-posting.
_state_lock = threading.Lock()
_running: set[tuple[str, int]] = set()
_rerun: set[tuple[str, int]] = set()


def review_pull_request(installation_id: int, repo: str, number: int) -> None:
    key = (repo, number)
    with _state_lock:
        if key in _running:
            _rerun.add(key)
            log.info("review already running for %s#%s; queued one rerun", repo, number)
            return
        _running.add(key)
    try:
        while True:
            _review_once(installation_id, repo, number)
            with _state_lock:
                if key not in _rerun:
                    return
                _rerun.discard(key)
    finally:
        with _state_lock:
            _running.discard(key)


def _review_once(installation_id: int, repo: str, number: int) -> None:
    gh = GitHubClient.for_installation(installation_id)
    llm = GeminiClient()

    pr = gh.get_pull(repo, number)
    if pr.get("state") != "open":
        return
    head_sha = pr["head"]["sha"]
    title, body = pr.get("title", ""), pr.get("body") or ""

    cfg = parse_config(gh.get_file_text(repo, ".reviewbot.yml", head_sha))
    files = gh.list_pr_files(repo, number)
    posted_bodies = gh.list_review_comment_bodies(repo, number) + gh.list_issue_comment_bodies(
        repo, number
    )
    already_posted = existing_fingerprints(posted_bodies)
    first_review = not any(
        SUMMARY_MARKER in b for b in gh.list_review_bodies(repo, number) + posted_bodies
    )

    skipped: list[str] = []
    candidates: list[tuple[str, Finding]] = []
    reviewed = 0

    for f in files:
        name, patch = f["filename"], f.get("patch")
        reason = skip_reason(name, patch, cfg)
        if reason is None and reviewed >= cfg.max_files:
            reason = "file limit reached"
        if reason:
            skipped.append(f"{name} ({reason})")
            continue
        reviewed += 1
        try:
            for finding in review_file(llm, name, patch, cfg, title, body):
                if fingerprint(name, finding.message) not in already_posted:
                    candidates.append((name, finding))
        except Exception:  # one bad file should not sink the whole review
            log.exception("failed reviewing %s", name)
            skipped.append(f"{name} (error)")

    chosen = rank_and_cap(candidates, cfg.max_comments)

    summary = (
        summarize(llm, title, body, [(f["filename"], f["additions"], f["deletions"]) for f in files])
        if first_review
        else None
    )
    review_body = render_summary(summary, reviewed, skipped, len(chosen))
    comments = [
        {"path": p, "line": f.line, "side": "RIGHT", "body": render_comment(p, f)} for p, f in chosen
    ]

    if not comments and not first_review:
        log.info("nothing new to report for %s#%s", repo, number)
        return

    # Don't post stale comments if the author pushed while we were reviewing.
    if gh.get_pull(repo, number)["head"]["sha"] != head_sha:
        log.info("head moved during review of %s#%s; dropping stale result", repo, number)
        return

    try:
        gh.create_review(repo, number, head_sha, review_body, comments)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 422:
            raise
        # A comment pointed at a line GitHub rejected: degrade to one PR comment. Keep the
        # hidden markers so the next push still dedupes against what was posted here.
        fallback = review_body + "\n\n" + "\n\n".join(
            f"`{p}:{f.line}`\n{render_comment(p, f)}" for p, f in chosen
        )
        gh.post_issue_comment(repo, number, fallback)
