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
SEVERITY_RE = re.compile(r"\*\*(\w+)\*\*:")
NEAR_LINES = 1  # same-severity comments this close together are treated as the same issue


def comment_severity(body: str) -> str | None:
    """Recover the severity from a comment we rendered, e.g. '🔒 **Security**: ...'."""
    match = SEVERITY_RE.search(body or "")
    severity = match.group(1).lower() if match else None
    return severity if severity in SEVERITY_ORDER else None


def near_existing(path: str, f: Finding, existing: list[tuple[str, int, str | None]]) -> bool:
    """True if we already commented on this issue, possibly anchored one line away.

    The model sometimes reports one problem on two adjacent lines (e.g. the line that
    builds a SQL string and the line that executes it) across different runs.
    """
    return any(
        p == path and sev == f.severity and abs(line - f.line) <= NEAR_LINES
        for p, line, sev in existing
    )


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


def fingerprint(path: str, severity: str, code: str) -> str:
    """Identify a finding by the flagged source line, not by the model's wording.

    The model rephrases the same problem on every run, so hashing its message defeats
    dedupe. Path + severity + the code on the flagged line is stable across pushes and
    across line-number shifts, and changes only when the author edits that line.
    """
    normalized = re.sub(r"\s+", " ", code).strip()
    return hashlib.sha1(f"{path}:{severity}:{normalized}".encode()).hexdigest()[:12]


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


def render_comment(path: str, f: Finding, code: str = "") -> str:
    body = f"{EMOJI[f.severity]} **{f.severity.capitalize()}**: {f.message}"
    if f.suggestion:
        body += f"\n\n```suggestion\n{f.suggestion}\n```"
    return f"{body}\n\n<!-- reviewbot:fp={fingerprint(path, f.severity, code or f.message)} -->"


def existing_fingerprints(comment_bodies: list[str]) -> set[str]:
    found: set[str] = set()
    for body in comment_bodies:
        found.update(MARKER_RE.findall(body or ""))
    return found


def summarize(llm: GeminiClient, pr_title: str, pr_body: str, files: list[tuple[str, int, int]]) -> PRSummary | None:
    overview = "\n".join(f"- {name} (+{add}/-{dele})" for name, add, dele in files[:40])
    return llm.generate(SUMMARY_SYSTEM, summary_prompt(pr_title, pr_body, overview), PRSummary)


RISK_LEVELS = ["low", "medium", "high"]


def effective_risk(model_risk: str, findings: list[Finding]) -> str:
    """The summary model never sees the findings, so raise its risk guess to match them."""
    floor = "low"
    if any(f.severity == "security" for f in findings):
        floor = "high"
    elif any(f.severity == "bug" for f in findings):
        floor = "medium"
    return max(model_risk, floor, key=RISK_LEVELS.index)


def render_summary(
    summary: PRSummary | None,
    reviewed: int,
    skipped: list[str],
    posted: int,
    findings: list[Finding] | None = None,
) -> str:
    lines = [SUMMARY_MARKER, "## 🤖 Automated review"]
    if summary:
        level = effective_risk(summary.risk, findings or [])
        risk = {"low": "🟢", "medium": "🟡", "high": "🔴"}[level]
        lines += [summary.summary, "", f"**Risk:** {risk} {level}", ""]
        lines += [f"- {item}" for item in summary.walkthrough[:8]]
        lines.append("")
    lines.append(f"Reviewed {reviewed} file(s); posted {posted} comment(s).")
    if skipped:
        lines.append(f"<sub>Skipped: {', '.join(skipped[:10])}</sub>")
    return "\n".join(lines)
