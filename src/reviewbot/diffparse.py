"""Unified-diff parsing.

GitHub only accepts inline review comments on lines that appear in the diff
(added lines or context lines on the RIGHT side). Getting this wrong is the
most common bug in review bots, so it lives in one small, well-tested module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SNAP_DISTANCE = 3


@dataclass
class ParsedPatch:
    added_lines: set[int] = field(default_factory=set)
    commentable_lines: set[int] = field(default_factory=set)
    numbered: str = ""  # patch rendered with new-file line numbers, for the LLM


def parse_patch(patch: str) -> ParsedPatch:
    """Parse one file's patch (the part from the first @@ onward)."""
    parsed = ParsedPatch()
    out: list[str] = []
    new_line = 0

    for raw in patch.splitlines():
        hunk = HUNK_RE.match(raw)
        if hunk:
            new_line = int(hunk.group(3))
            out.append(raw)
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        if raw.startswith("+"):
            parsed.added_lines.add(new_line)
            parsed.commentable_lines.add(new_line)
            out.append(f"{new_line:>5} | {raw}")
            new_line += 1
        elif raw.startswith("-"):
            out.append(f"      | {raw}")
        else:  # context line (leading space) or blank
            parsed.commentable_lines.add(new_line)
            out.append(f"{new_line:>5} | {raw}")
            new_line += 1

    parsed.numbered = "\n".join(out)
    return parsed


def snap_to_commentable(line: int, parsed: ParsedPatch) -> int | None:
    """Return a valid diff line for a finding, or None if nothing is close enough."""
    if line in parsed.commentable_lines:
        return line
    nearby = [n for n in parsed.added_lines if abs(n - line) <= SNAP_DISTANCE]
    if not nearby:
        return None
    return min(nearby, key=lambda n: (abs(n - line), n))


def split_git_diff(diff_text: str) -> dict[str, str]:
    """Split `git diff` output into {filename: patch}. Skips binary and deleted files."""
    files: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    in_hunks = False

    def flush() -> None:
        if current and buf:
            files[current] = "\n".join(buf)

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current, buf, in_hunks = None, [], False
        elif line.startswith("+++ ") and not in_hunks:
            target = line[4:].split("\t")[0].strip()
            current = None if target == "/dev/null" else target.removeprefix("b/")
        elif line.startswith("@@"):
            in_hunks = True
            buf.append(line)
        elif in_hunks:
            buf.append(line)
    flush()
    return files
