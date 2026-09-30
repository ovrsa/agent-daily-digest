"""Run the Judge against the real model on the audit fixture with known problems.

This is not a test: it calls the model once (about 0.05-0.15 USD at list
price, more when a report is retried). Run it by hand when the Judge prompt
changes, and compare the tables with the last run recorded in the PR.

    .venv/bin/python evals/judge_fixed_set.py

The planted problems and the control article are in `tests/judge_support.py`.
A known problem counts as found when a finding names one of its articles with
one of its categories. A finding on the control article, other than
duplication, is a false positive; every other finding is listed for the human
reviewer. Nothing is written to disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from judge_support import CONTROL, KNOWN_ISSUES, OUTPUT, PACKETS, library  # noqa: E402

from agent_daily_digest.judge import Judge, render_report
from agent_daily_digest.llm.client import invoke_structured
from agent_daily_digest.llm.pricing import load_pricing


def main() -> None:
    judge = Judge(
        invoke=invoke_structured,
        pricing=load_pricing(ROOT / "config.json"),
        model="claude-sonnet-5",
        library=library(),
        max_call_usd=0.5,
    )
    result = judge.audit(OUTPUT, PACKETS)
    for attempt in result.metrics.attempts:
        issues = ", ".join(f"{i.loc}:{i.type}" for i in attempt.validation_issues) or "-"
        kind = attempt.error.kind.value if attempt.error else "ok"
        print(f"attempt {attempt.attempt_number}: {kind} tokens={attempt.usage} cost={attempt.cost} issues={issues}")
    print(f"targets: {[t.article_id for t in result.targets]}")
    if not result.succeeded:
        print(f"judge failed: {result.error}")
        return
    findings = result.report.findings

    matched: set[str] = set()
    print("\n| known problem | found by | severity / confidence |")
    print("|---|---|---|")
    for issue in KNOWN_ISSUES:
        hits = [f for f in findings if f.article_id in issue.articles and f.assessment.category.value in issue.categories]
        matched.update(f.finding_id for f in hits)
        found = ", ".join(f"{f.finding_id} ({f.assessment.category.value})" for f in hits) or "MISSED"
        grades = ", ".join(f"{f.assessment.severity.value}/{f.assessment.confidence.value}" for f in hits) or "-"
        print(f"| {issue.label} | {found} | {grades} |")

    print("\n| other finding | article | category | severity / confidence | verdict |")
    print("|---|---|---|---|---|")
    for f in findings:
        if f.finding_id in matched:
            continue
        a = f.assessment
        verdict = "FALSE POSITIVE" if f.article_id == CONTROL and a.category.value != "duplication" else "review"
        print(f"| {f.finding_id} | {f.article_id} | {a.category.value} | {a.severity.value}/{a.confidence.value} | {verdict} |")

    print("\n" + render_report(result, PACKETS))
    print(f"total estimated cost: {result.metrics.total_cost_usd} USD")


if __name__ == "__main__":
    main()
