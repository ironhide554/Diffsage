"""CLI: review your git diff before you push, or the triggering PR inside GitHub Actions.

    reviewbot review --base main
    reviewbot github-action
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from .config import parse_config
from .diffparse import split_git_diff
from .llm import GeminiClient
from .reviewer import rank_and_cap, review_file, skip_reason
from .service import review_with_token

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
    try:
        llm = GeminiClient()
    except RuntimeError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return 2

    results = []
    for name, patch in split_git_diff(diff).items():
        reason = skip_reason(name, patch, cfg)
        if reason:
            console.print(f"[dim]skip {name}: {reason}[/dim]")
            continue
        with console.status(f"Reviewing {name}..."):
            results += [(name, f) for f in review_file(llm, name, patch, cfg, args.title)]

    results = rank_and_cap(results, cfg.max_comments)
    if not results:
        console.print("[green]No issues found.[/green]")
        return 0

    for name, f in results:
        body = escape(f.message)
        if f.suggestion:
            body += f"\n\n[bold]Suggestion:[/bold] {escape(f.suggestion)}"
        title = escape(f"{name}:{f.line}") + f"  \\[{f.severity}]"
        console.print(Panel(body, title=title, border_style="yellow"))
    console.print(f"\n{len(results)} issue(s) found.")
    return 1 if args.fail_on_findings else 0


def cmd_github_action(args: argparse.Namespace) -> int:
    """Review the pull request that triggered a GitHub Actions run."""
    event_path, token = os.environ.get("GITHUB_EVENT_PATH"), os.environ.get("GITHUB_TOKEN")
    if not event_path or not token:
        console.print("[red]GITHUB_EVENT_PATH and GITHUB_TOKEN are required (run inside Actions).[/red]")
        return 2
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = event.get("pull_request")
    if not pr:
        console.print("Not a pull_request event; nothing to do.")
        return 0
    if pr.get("draft"):
        console.print("Draft pull request; skipping.")
        return 0
    try:
        review_with_token(token, event["repository"]["full_name"], pr["number"])
    except Exception as exc:  # surface a one-line reason instead of a traceback in the job log
        console.print(f"[red]review failed:[/red] {escape(str(exc))}")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="reviewbot")
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="review the current branch's diff")
    review.add_argument("--base", default="main", help="base branch to diff against (default: main)")
    review.add_argument("--staged", action="store_true", help="review staged changes only")
    review.add_argument("--title", default="", help="optional PR title to give the reviewer context")
    review.add_argument("--fail-on-findings", action="store_true", help="exit 1 if anything is found")
    review.set_defaults(func=cmd_review)
    action = sub.add_parser("github-action", help="review the PR that triggered a GitHub Actions run")
    action.set_defaults(func=cmd_github_action)
    load_dotenv()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
