"""Core review logic: LLM pass, line mapping, noise filtering, skeptic pass, dedupe."""

from __future__ import annotations

import hashlib
import logging
import re

from .config import Config
from .diffparse import ParsedPatch, parse_patch, snap_to_commentable
from .llm import GeminiClient
from .models import SEVERITY_ORDER, FileReview, Finding, PRSummary, Verdicts
from .prompts import (
    REVIEW_SYSTEM,
    SUMMARY_SYSTEM,
    VERIFY_SYSTEM,
    review_prompt,
    summary_prompt,
    verify_prompt,
)

log = logging.getLogger("reviewbot")

SUMMARY_MARKER = "<!-- reviewbot:summary -->"
MARKER_RE = re.compile(r"<!-- reviewbot:fp=([0-9a-f]{12}) -->")
EMOJI = {"security": "🔒", "bug": "🐛", "performance": "⚡", "maintainability": "🧹", "nit": "💬"}


def skip_reason(filename: str, patch: str | None, cfg: Config) -> str | None:
    if not patch:
        return "no textual diff"
    if cfg.is_ignored(filename):
        return "ignored by config"
    if len(patch) > cfg.max_patch_chars:
        return "diff too large"
    return None


def fingerprint(path: str, message: str) -> str:
    """Stable across pushes: ignores the line number, which shifts as code changes."""
    normalized = re.sub(r"\W+", " ", message.lower()).strip()[:80]
    return hashlib.sha1(f"{path}:{normalized}".encode()).hexdigest()[:12]


def review_file(
    llm: GeminiClient,
    filename: str,
    patch: str,
    cfg: Config,
    pr_title: str = "",
    pr_body: str = "",
) -> list[Finding]:
    parsed = parse_patch(patch)
    if not parsed.added_lines:
        return []

    result = llm.generate(
        REVIEW_SYSTEM,
        review_prompt(filename, parsed.numbered, pr_title, pr_body, cfg.instructions),
        FileReview,
    )
    if result is None:
        log.warning("no valid model output for %s; skipping file", filename)
        return []

    findings = filter_findings(result.findings, parsed, cfg)
    if cfg.verify and findings:
        findings = verify_findings(llm, filename, parsed, findings)
    return findings


def filter_findings(findings: list[Finding], parsed: ParsedPatch, cfg: Config) -> list[Finding]:
    kept: list[Finding] = []
    for f in findings:
        if f.confidence < cfg.min_confidence or f.severity not in cfg.severities:
            continue
        line = snap_to_commentable(f.line, parsed)
        if line is None:
            continue
        suggestion = f.suggestion
        if suggestion is not None and ("\n" in suggestion.strip("\n") or not suggestion.strip()):
            suggestion = None  # a suggestion must replace exactly one line
        kept.append(f.model_copy(update={"line": line, "suggestion": suggestion}))
    return kept


def verify_findings(
    llm: GeminiClient, filename: str, parsed: ParsedPatch, findings: list[Finding]
) -> list[Finding]:
    payload = "\n".join(
        f'{i}. line {f.line} [{f.severity}] {f.message}' for i, f in enumerate(findings)
    )
    verdicts = llm.generate(VERIFY_SYSTEM, verify_prompt(filename, parsed.numbered, payload), Verdicts)
    if verdicts is None:
        return findings  # fail open: keep the first-pass findings
    # Only an explicit valid=false rejects a finding; missing verdicts fail open.
    rejected = {v.index for v in verdicts.verdicts if not v.valid}
    return [f for i, f in enumerate(findings) if i not in rejected]


def rank_and_cap(findings: list[tuple[str, Finding]], cap: int) -> list[tuple[str, Finding]]:
    ordered = sorted(findings, key=lambda pf: (SEVERITY_ORDER[pf[1].severity], -pf[1].confidence))
    return ordered[:cap]


def render_comment(path: str, f: Finding) -> str:
    body = f"{EMOJI[f.severity]} **{f.severity.capitalize()}**: {f.message}"
    if f.suggestion:
        body += f"\n\n```suggestion\n{f.suggestion}\n```"
    return f"{body}\n\n<!-- reviewbot:fp={fingerprint(path, f.message)} -->"


def existing_fingerprints(comment_bodies: list[str]) -> set[str]:
    found: set[str] = set()
    for body in comment_bodies:
        found.update(MARKER_RE.findall(body or ""))
    return found


def summarize(llm: GeminiClient, pr_title: str, pr_body: str, files: list[tuple[str, int, int]]) -> PRSummary | None:
    overview = "\n".join(f"- {name} (+{add}/-{dele})" for name, add, dele in files[:40])
    return llm.generate(SUMMARY_SYSTEM, summary_prompt(pr_title, pr_body, overview), PRSummary)


def render_summary(summary: PRSummary | None, reviewed: int, skipped: list[str], posted: int) -> str:
    lines = [SUMMARY_MARKER, "## 🤖 Automated review"]
    if summary:
        risk = {"low": "🟢", "medium": "🟡", "high": "🔴"}[summary.risk]
        lines += [summary.summary, "", f"**Risk:** {risk} {summary.risk}", ""]
        lines += [f"- {item}" for item in summary.walkthrough[:8]]
        lines.append("")
    lines.append(f"Reviewed {reviewed} file(s); posted {posted} comment(s).")
    if skipped:
        lines.append(f"<sub>Skipped: {', '.join(skipped[:10])}</sub>")
    return "\n".join(lines)
