# Changelog

## 0.1.0

First release.

### Added
- AI pull request reviewer powered by Gemini: structured findings per file, mapped to valid diff lines,
  confidence and severity filtering, a second "skeptic" verification pass, ranking and a comment cap.
- Three ways to run it: a reusable **GitHub Action**, a **GitHub App** webhook server (FastAPI, HMAC-verified,
  Docker image included) and a **CLI** (`reviewbot review`, `reviewbot github-action`).
- Per-repo configuration through `.reviewbot.yml` (confidence, severities, ignore patterns, instructions).
- Repeat protection: each comment carries a hidden fingerprint of the flagged code, and findings on or next to an
  already-reported line are skipped, so pushes don't produce duplicate reviews.
- One-click *Commit suggestion* fixes, offered only when a single line fully fixes the problem.
- Summary with a risk level that is raised to match the findings actually posted.
- One review at a time per PR, with a single rerun queued if a push arrives mid-review; stale results are dropped.
- Evaluation harness (`eval/score.py`) with 7 labeled cases, and 51 offline tests.
- CI on Python 3.10 to 3.13, and a workflow that has Diffsage review its own pull requests.

### Known limitations
- The Action cannot review pull requests from forks (GitHub withholds secrets); use the App for those.
- Only single-line suggestions; two different same-severity issues on adjacent lines can be merged.
- Only the diff is sent to the model, and large PRs are slow on the free tier.
