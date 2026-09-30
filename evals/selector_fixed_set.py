"""Run the Selector against the real model on the fixed evaluation set.

This is not a test: it calls the model once (about 0.05-0.15 USD at list
price, more when a decision is retried). Run it by hand when the Selector
prompt changes, and compare the table with the last run recorded in the PR.

    .venv/bin/python evals/selector_fixed_set.py

Expected decisions are in `tests/selector_support.py`. A row marked `review`
is a defensible difference for the human reviewer; `MISMATCH` is an error.
Nothing is written to disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from agent_daily_digest.llm.client import invoke_structured
from agent_daily_digest.llm.pricing import load_pricing
from agent_daily_digest.select import Selector  # noqa: E402
from selector_support import EXPECTED, PACKETS, library  # noqa: E402


def main() -> None:
    selector = Selector(
        invoke=invoke_structured,
        pricing=load_pricing(ROOT / "config.json"),
        model="claude-sonnet-5",
        library=library(),
        max_call_usd=0.5,
    )
    result = selector.select(PACKETS)
    metrics = result.metrics
    for attempt in metrics.attempts:
        issues = ", ".join(f"{i.loc}:{i.type}" for i in attempt.validation_issues) or "-"
        kind = attempt.error.kind.value if attempt.error else "ok"
        print(f"attempt {attempt.attempt_number}: {kind} tokens={attempt.usage} cost={attempt.cost} issues={issues}")
    if not result.succeeded:
        print(f"selector failed: {result.error}")
        return
    output = result.output
    print("\n| article | expected | decision | tier / order | verdict |")
    print("|---|---|---|---|---|")
    for article_id, expected in EXPECTED.items():
        decision, tier = output.decision_of(article_id)
        order = ""
        if tier is not None:
            bucket = output.must_read if tier.value == "must_read" else output.worth_knowing
            order = f"{tier.value} #{[a.article_id for a in bucket].index(article_id) + 1}"
        chosen = "include" if decision.value == "included" else "exclude"
        verdict = "ok" if expected == chosen else ("review" if expected == "either" else "MISMATCH")
        print(f"| {article_id} | {expected} | {chosen} | {order or '-'} | {verdict} |")
    print(f"\nduplicate groups: {[(g.representative_id, g.duplicate_ids) for g in output.duplicate_groups]}")
    for article in output.included:
        e = article.entry
        print(f"\n## {article.article_id}\nscores: {article.scores.model_dump()}\nreason: {article.decision_reason}")
        print(f"- 一覧の1行: {e.headline}")
        print(f"- 何をしたか: {e.what_happened.text} {e.what_happened.evidence_ids}")
        print(f"- 読む理由: {e.why_read}")
        print(f"- 根拠: {e.evidence.text} {e.evidence.evidence_ids}")
        if e.caveat:
            print(f"- 留保: {e.caveat.text} {e.caveat.evidence_ids}")
    for article in output.excluded:
        print(f"\n[excluded] {article.article_id}: {article.decision_reason}")
    print(f"\ntotal estimated cost: {metrics.total_cost_usd} USD")


if __name__ == "__main__":
    main()
