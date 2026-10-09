"""Score the reviewer against labeled cases.

Each file in eval/cases/*.json looks like:

    {
      "name": "sql-injection-in-search",
      "path": "app/search.py",
      "patch": "@@ -1,3 +1,4 @@ ...",
      "expected": [{"line": 12, "severity": "security"}]
    }

A produced finding counts as a true positive if it lands within TOLERANCE lines of an
expected issue. Cases with an empty "expected" list measure false positives.

    python eval/score.py            # prints a Markdown table you can paste into the README
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reviewbot.config import Config  # noqa: E402
from reviewbot.llm import GeminiClient  # noqa: E402
from reviewbot.reviewer import review_file  # noqa: E402

TOLERANCE = 2
CASES = Path(__file__).parent / "cases"


def main() -> None:
    llm = GeminiClient()
    cfg = Config()
    tp = fp = fn = 0
    rows = []

    for path in sorted(CASES.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        found = review_file(llm, case["path"], case["patch"], cfg)
        expected = list(case["expected"])

        matched = 0
        for f in found:
            hit = next((e for e in expected if abs(e["line"] - f.line) <= TOLERANCE), None)
            if hit:
                expected.remove(hit)
                matched += 1
        case_fp = len(found) - matched
        case_fn = len(expected)
        tp, fp, fn = tp + matched, fp + case_fp, fn + case_fn
        rows.append((case["name"], matched, case_fp, case_fn))

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0

    print(f"Model: {llm.model}\n")
    print("| Case | Caught | False alarms | Missed |")
    print("|---|---|---|---|")
    for name, m, f_p, f_n in rows:
        print(f"| {name} | {m} | {f_p} | {f_n} |")
    print(f"\n**Precision:** {precision:.0%}  **Recall:** {recall:.0%}  ({tp} TP, {fp} FP, {fn} FN)")


if __name__ == "__main__":
    main()
