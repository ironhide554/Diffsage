import argparse
import json
import subprocess

from reviewbot import cli
from reviewbot.models import Finding


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def make_repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "my app.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "my app.py").write_text("x = 1\ny = eval(input())\n", encoding="utf-8")
    git(tmp_path, "commit", "-qam", "change")


def args(**kw):
    base = {"base": "main", "staged": False, "fail_on_findings": True, "title": ""}
    return argparse.Namespace(**{**base, **kw})


def test_review_reports_findings_for_filename_with_space(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    seen = []

    def fake_review(llm, name, patch, cfg, title=""):
        seen.append(name)
        return [Finding(line=2, severity="security", message="eval [bad]", confidence=0.9)]

    monkeypatch.setattr(cli, "GeminiClient", lambda: object())
    monkeypatch.setattr(cli, "review_file", fake_review)
    assert cli.cmd_review(args()) == 1  # "[bad]" in the message must not break Rich markup
    assert seen == ["my app.py"]


def test_missing_api_key_is_friendly(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert cli.cmd_review(args()) == 2


def test_bad_base_branch_returns_2(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.cmd_review(args(base="nope")) == 2


def test_github_action_requires_env(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.cmd_github_action(argparse.Namespace()) == 2


def _event(tmp_path, monkeypatch, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")


def test_github_action_reviews_the_pr(tmp_path, monkeypatch):
    _event(tmp_path, monkeypatch, {"pull_request": {"number": 9}, "repository": {"full_name": "o/r"}})
    calls = []
    monkeypatch.setattr(cli, "review_with_token", lambda *a: calls.append(a))
    assert cli.cmd_github_action(argparse.Namespace()) == 0
    assert calls == [("tok", "o/r", 9)]


def test_github_action_skips_drafts_and_non_pr_events(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "review_with_token", lambda *a: calls.append(a))
    _event(tmp_path, monkeypatch, {"pull_request": {"number": 9, "draft": True}})
    assert cli.cmd_github_action(argparse.Namespace()) == 0
    _event(tmp_path, monkeypatch, {"push": {}})
    assert cli.cmd_github_action(argparse.Namespace()) == 0
    assert calls == []


def test_github_action_failure_returns_1(tmp_path, monkeypatch):
    _event(tmp_path, monkeypatch, {"pull_request": {"number": 9}, "repository": {"full_name": "o/r"}})

    def boom(*a):
        raise RuntimeError("quota [exhausted]")

    monkeypatch.setattr(cli, "review_with_token", boom)
    assert cli.cmd_github_action(argparse.Namespace()) == 1
