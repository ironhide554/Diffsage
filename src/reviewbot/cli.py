"""Local CLI: review your current git diff before you push.

    reviewbot review --base main
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .config import parse_config
from .diffparse import split_git_diff
from .llm import GeminiClient
from .reviewer import rank_and_cap, review_file, skip_reason

console = Console()


def _git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], capture_output=True, text=True, check=True).stdout


def cmd_review(args: argparse.Namespace) -> int:
    try:
        diff = _git("diff", "--unified=3", f"{args.base}...HEAD") if not args.staged else _git(
            "diff", "--unified=3", "--cached"
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]git failed:[/red] {exc.stderr.strip()}")
        return 2

    cfg_path = Path(".reviewbot.yml")
    cfg = parse_config(cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else None)
    llm = GeminiClient()

    results = []
    for name, patch in split_git_diff(diff).items():
        reason = skip_reason(name, patch, cfg)
        if reason:
            console.print(f"[dim]skip {name}: {reason}[/dim]")
            continue
        with console.status(f"Reviewing {name}..."):
            results += [(name, f) for f in review_file(llm, name, patch, cfg)]

    results = rank_and_cap(results, cfg.max_comments)
    if not results:
        console.print("[green]No issues found.[/green]")
        return 0

    for name, f in results:
        body = f.message + (f"\n\n[bold]Suggestion:[/bold] {f.suggestion}" if f.suggestion else "")
        console.print(Panel(body, title=f"{name}:{f.line}  [{f.severity}]", border_style="yellow"))
    console.print(f"\n{len(results)} issue(s) found.")
    return 1 if args.fail_on_findings else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="reviewbot")
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="review the current branch's diff")
    review.add_argument("--base", default="main", help="base branch to diff against (default: main)")
    review.add_argument("--staged", action="store_true", help="review staged changes only")
    review.add_argument("--fail-on-findings", action="store_true", help="exit 1 if anything is found")
    review.set_defaults(func=cmd_review)
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
