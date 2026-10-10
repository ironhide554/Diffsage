import httpx
import pytest

from reviewbot import service
from reviewbot.models import Finding
from reviewbot.reviewer import SUMMARY_MARKER

PATCH = "@@ -1,2 +1,3 @@\n a\n+b = eval(x)\n c\n"


def finding(msg="eval on user input", line=2):
    return Finding(line=line, severity="security", message=msg, confidence=0.9)


class FakeGH:
    def __init__(self, review_comments=(), reviews=(), issue_comments=(), create_error=None):
        self.review_comments, self.reviews = list(review_comments), list(reviews)
        self.issue_comments = list(issue_comments)
        self.create_error = create_error
        self.created: list[tuple] = []
        self.issue_posts: list[str] = []
        self.head = "sha1"

    def get_pull(self, repo, number):
        return {"state": "open", "head": {"sha": self.head}, "title": "t", "body": "b"}

    def get_file_text(self, *a):
        return None

    def list_pr_files(self, *a):
        return [{"filename": "a.py", "patch": PATCH, "additions": 1, "deletions": 0}]

    def list_review_comment_bodies(self, *a):
        return self.review_comments

    def list_issue_comment_bodies(self, *a):
        return self.issue_comments

    def list_review_bodies(self, *a):
        return self.reviews

    def create_review(self, repo, number, sha, body, comments):
        if self.create_error:
            raise self.create_error
        self.created.append((body, comments))

    def post_issue_comment(self, repo, number, body):
        self.issue_posts.append(body)


@pytest.fixture()
def setup(monkeypatch):
    def _setup(gh, findings=None):
        monkeypatch.setattr(service.GitHubClient, "for_installation", classmethod(lambda c, i: gh))
        monkeypatch.setattr(service, "GeminiClient", lambda: object())
        monkeypatch.setattr(service, "review_file", lambda *a, **k: findings or [finding()])
        monkeypatch.setattr(service, "summarize", lambda *a, **k: None)
        service._running.clear()
        service._rerun.clear()
        return gh

    return _setup


def test_first_review_posts_summary_and_comments(setup):
    gh = setup(FakeGH())
    service.review_pull_request(1, "o/r", 1)
    body, comments = gh.created[0]
    assert SUMMARY_MARKER in body
    assert comments[0]["path"] == "a.py" and comments[0]["line"] == 2


def test_already_posted_finding_is_not_repeated(setup):
    first = setup(FakeGH())
    service.review_pull_request(1, "o/r", 1)
    posted = first.created[0][1][0]["body"]
    gh = setup(FakeGH(review_comments=[posted], reviews=[SUMMARY_MARKER]))
    service.review_pull_request(1, "o/r", 1)
    assert gh.created == []  # nothing new and the summary was already posted


def test_reworded_finding_on_same_line_is_not_reposted(setup):
    # Regression: the live bot reposted comments because the model phrased the same
    # problem differently on the next run. Dedupe must not depend on the wording.
    first = setup(FakeGH(), findings=[finding("Using eval on input is dangerous.")])
    service.review_pull_request(1, "o/r", 1)
    posted = first.created[0][1][0]["body"]
    gh = setup(
        FakeGH(review_comments=[posted], reviews=[SUMMARY_MARKER]),
        findings=[finding("Untrusted data reaches eval(), allowing code execution.")],
    )
    service.review_pull_request(1, "o/r", 1)
    assert gh.created == []


def test_new_finding_posted_without_a_second_summary(setup):
    gh = setup(FakeGH(reviews=[SUMMARY_MARKER]), findings=[finding("different problem")])
    service.review_pull_request(1, "o/r", 1)
    assert len(gh.created) == 1


def test_422_falls_back_to_issue_comment_with_markers(setup):
    err = httpx.HTTPStatusError(
        "x", request=httpx.Request("POST", "http://x"), response=httpx.Response(422)
    )
    gh = setup(FakeGH(create_error=err))
    service.review_pull_request(1, "o/r", 1)
    assert "reviewbot:fp=" in gh.issue_posts[0] and SUMMARY_MARKER in gh.issue_posts[0]


def test_stale_head_is_dropped(setup):
    gh = setup(FakeGH())
    original = gh.get_pull
    calls = {"n": 0}

    def moving(repo, number):
        calls["n"] += 1
        if calls["n"] > 1:
            gh.head = "sha2"
        return original(repo, number)

    gh.get_pull = moving
    service.review_pull_request(1, "o/r", 1)
    assert gh.created == []


def test_concurrent_push_queues_one_rerun(setup, monkeypatch):
    setup(FakeGH())
    runs = []
    orig = service._review_once

    def once(*a):
        runs.append(1)
        if len(runs) == 1:
            service.review_pull_request(1, "o/r", 1)  # push arrives mid-review
            service.review_pull_request(1, "o/r", 1)  # and another: still just one rerun
        orig(*a)

    monkeypatch.setattr(service, "_review_once", once)
    service.review_pull_request(1, "o/r", 1)
    assert len(runs) == 2


def test_review_with_token_uses_a_plain_client(setup, monkeypatch):
    gh = setup(FakeGH())
    monkeypatch.setattr(service, "GitHubClient", lambda token: gh)
    service.review_with_token("tok", "o/r", 1)
    assert gh.created and SUMMARY_MARKER in gh.created[0][0]
