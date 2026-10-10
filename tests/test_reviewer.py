from reviewbot.config import Config, parse_config
from reviewbot.diffparse import parse_patch
from reviewbot.models import Finding
from reviewbot.reviewer import (
    existing_fingerprints,
    filter_findings,
    fingerprint,
    rank_and_cap,
    render_comment,
    skip_reason,
)

PATCH = "@@ -1,2 +1,3 @@\n a\n+b = eval(x)\n c\n"


def finding(**kw):
    base = {"line": 2, "severity": "security", "message": "eval on user input", "confidence": 0.9}
    base.update(kw)
    return Finding(**base)


def test_filter_drops_low_confidence_and_nits():
    cfg = Config()
    parsed = parse_patch(PATCH)
    kept = filter_findings(
        [finding(), finding(confidence=0.3), finding(severity="nit")], parsed, cfg
    )
    assert len(kept) == 1


def test_filter_snaps_or_drops_out_of_range_lines():
    parsed = parse_patch(PATCH)
    kept = filter_findings([finding(line=4), finding(line=99)], parsed, Config())
    assert [f.line for f in kept] == [2]


def test_fingerprint_ignores_line_and_punctuation():
    assert fingerprint("a.py", "Eval on user input!") == fingerprint("a.py", "eval on  user input")
    assert fingerprint("a.py", "x") != fingerprint("b.py", "x")


def test_marker_round_trip_for_dedupe():
    body = render_comment("a.py", finding())
    assert existing_fingerprints([body]) == {fingerprint("a.py", "eval on user input")}


def test_suggestion_block_rendered():
    body = render_comment("a.py", finding(suggestion="b = ast.literal_eval(x)"))
    assert "```suggestion\nb = ast.literal_eval(x)\n```" in body


def test_rank_puts_security_first_and_caps():
    items = [("a", finding(severity="bug")), ("b", finding(severity="security"))]
    ranked = rank_and_cap(items, 1)
    assert ranked[0][0] == "b"


def test_skip_reasons():
    cfg = Config()
    assert skip_reason("poetry.lock", "@@", cfg) == "ignored by config"
    assert skip_reason("a.py", None, cfg) == "no textual diff"
    assert skip_reason("a.py", "x" * (cfg.max_patch_chars + 1), cfg) == "diff too large"
    assert skip_reason("src/a.py", "@@", cfg) is None


def test_config_parsing_is_defensive():
    cfg = parse_config("min_confidence: 5\nmax_comments: 3\nseverities: [bug, bogus]\nignore: ['docs/*']")
    assert cfg.min_confidence == 1.0
    assert cfg.max_comments == 3
    assert cfg.severities == ["bug"]
    assert cfg.is_ignored("docs/readme.md")
    assert parse_config("::: not yaml :::").max_comments == 10


def test_verify_fails_open_on_missing_verdicts():
    from reviewbot.models import Verdict, Verdicts
    from reviewbot.reviewer import verify_findings

    class Stub:
        def __init__(self, v):
            self.v = v

        def generate(self, *a, **k):
            return self.v

    parsed = parse_patch(PATCH)
    fs = [finding(), finding(message="other")]
    assert verify_findings(Stub(Verdicts(verdicts=[])), "a.py", parsed, fs) == fs
    out = verify_findings(
        Stub(Verdicts(verdicts=[Verdict(index=0, valid=False, reason="x")])), "a.py", parsed, fs
    )
    assert out == [fs[1]]


def test_multiline_suggestion_dropped():
    kept = filter_findings([finding(suggestion="a\nb")], parse_patch(PATCH), Config())
    assert kept[0].suggestion is None


def test_ignore_defaults_can_be_disabled():
    cfg = parse_config("ignore_defaults: false\nignore: ['x/*']")
    assert not cfg.is_ignored("poetry.lock")
    assert cfg.is_ignored("x/a.py")


def test_risk_is_raised_to_match_findings():
    from reviewbot.reviewer import effective_risk

    assert effective_risk("low", [finding(severity="security")]) == "high"
    assert effective_risk("low", [finding(severity="bug")]) == "medium"
    assert effective_risk("high", [finding(severity="bug")]) == "high"  # never lowered
    assert effective_risk("low", []) == "low"
    assert effective_risk("low", [finding(severity="maintainability")]) == "low"


def test_summary_shows_adjusted_risk():
    from reviewbot.models import PRSummary
    from reviewbot.reviewer import render_summary

    summary = PRSummary(summary="adds a file", risk="low", walkthrough=["x"])
    out = render_summary(summary, 1, [], 1, [finding(severity="security")])
    assert "🔴 high" in out and "🟢" not in out
