"""Glue: review one pull request end to end and post the result."""

from __future__ import annotations

import logging

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


def review_pull_request(installation_id: int, repo: str, number: int) -> None:
    gh = GitHubClient.for_installation(installation_id)
    llm = GeminiClient()

    pr = gh.get_pull(repo, number)
    head_sha = pr["head"]["sha"]
    title, body = pr.get("title", ""), pr.get("body") or ""

    cfg = parse_config(gh.get_file_text(repo, ".reviewbot.yml", head_sha))
    files = gh.list_pr_files(repo, number)
    already_posted = existing_fingerprints(gh.list_review_comment_bodies(repo, number))

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

    first_review = not any(SUMMARY_MARKER in b for b in gh.list_review_bodies(repo, number))
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
    try:
        gh.create_review(repo, number, head_sha, review_body, comments)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 422:
            raise
        # A comment pointed at a line GitHub rejected: degrade to a single PR comment.
        fallback = review_body + "\n\n" + "\n".join(
            f"- `{p}:{f.line}`: {f.message}" for p, f in chosen
        )
        gh.post_issue_comment(repo, number, fallback)
