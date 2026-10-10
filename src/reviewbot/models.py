"""Structured-output schemas shared by the LLM layer and the reviewer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["security", "bug", "performance", "maintainability", "nit"]

SEVERITY_ORDER = {"security": 0, "bug": 1, "performance": 2, "maintainability": 3, "nit": 4}


class Finding(BaseModel):
    line: int = Field(description="New-file line number the comment attaches to.")
    severity: Severity
    message: str = Field(description="One to three sentences explaining the problem and why it matters.")
    suggestion: str | None = Field(
        default=None,
        description=(
            "Replacement code for exactly that one line, only if that line alone fully fixes "
            "the problem; otherwise null."
        ),
    )
    confidence: float = Field(description="0.0 to 1.0: how sure you are this is a real problem.")


class FileReview(BaseModel):
    findings: list[Finding]


class Verdict(BaseModel):
    index: int
    valid: bool
    reason: str


class Verdicts(BaseModel):
    verdicts: list[Verdict]


class PRSummary(BaseModel):
    summary: str = Field(description="Two or three sentences on what this PR does.")
    risk: Literal["low", "medium", "high"]
    walkthrough: list[str] = Field(description="One short bullet per meaningful change.")
