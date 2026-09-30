"""Real-model scope and tie-break evaluation for #40 (synthetic evidence).

Run: .venv/bin/python evals/backoffice_priority.py
Exits nonzero on a scope, ordering, quality, or Judge regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from selector_support import case, library, packet  # noqa: E402

from agent_daily_digest.judge import Judge  # noqa: E402
from agent_daily_digest.llm import invoke_structured  # noqa: E402
from agent_daily_digest.observe import load_pricing  # noqa: E402
from agent_daily_digest.select import Selector  # noqa: E402


def main() -> None:
    # Comparable evidence strength across distinct workflows. Personal comes
    # first in input so output cannot pass by copying it.
    cases = [
        case(
            "personal",
            "Hermes helps me book hobby club practice sessions",
            evidence=(
                (
                    "procedure",
                    "I use Hermes to check court availability against our club calendar, propose a practice slot, and wait for me to book it. The agent cannot make purchases.",
                ),
                (
                    "failure",
                    "The agent proposed a slot while the courts were closed; I added facility opening hours as a constraint and include the booking page in each proposal.",
                ),
            ),
            claims=(
                (
                    "what_happened",
                    "著者は Hermes で趣味の会の練習時間を提案させ、施設の営業時間を条件に加え、予約は自分で行う。",
                    (0, 1),
                    False,
                    (),
                ),
            ),
        ),
        case(
            "office",
            "Hermes prepares our purchasing status report",
            evidence=(
                (
                    "procedure",
                    "I use Hermes to gather supplier updates from a dedicated mailbox, draft a purchasing report, and save it for my approval. I send it manually.",
                ),
                (
                    "failure",
                    "The agent treated an old update as new; I added a date check and keep the original beside every draft.",
                ),
            ),
            claims=(
                (
                    "what_happened",
                    "著者は Hermes で購買状況報告を下書きし、日付を確認して原文を添え、人が送信する。",
                    (0, 1),
                    False,
                    (),
                ),
            ),
        ),
    ]
    lib = library()
    for c in cases:
        lib.add(c.document)
    packets = tuple(c.packet for c in cases) + (packet("backoffice_promo"), packet("usage_case"))
    kwargs = dict(
        invoke=invoke_structured,
        pricing=load_pricing(ROOT / "config.json"),
        model="claude-sonnet-5",
        library=lib,
        max_call_usd=0.5,
    )
    result = Selector(**kwargs).select(packets)
    if not result.succeeded:
        raise RuntimeError(str(result.error))
    order = [a.article_id for a in result.output.included]
    print("order:", order, flush=True)
    for a in (*result.output.included, *result.output.excluded):
        print(a.article_id, a.decision_reason, flush=True)
    assert all(a in order for a in ("personal", "office", "usage_case")), "use cases excluded"
    assert "backoffice_promo" not in order, "promotion must not benefit from preference"
    assert order.index("office") < order.index("personal"), "back-office tie-break missing"
    assert order.index("usage_case") < order.index("office"), "stronger coding case must keep priority"
    audit = Judge(**kwargs).audit(result.output, packets)
    if not audit.succeeded:
        raise RuntimeError(str(audit.error))
    for f in audit.report.findings:
        print(f.article_id, f.assessment.category.value, f.assessment.problem, flush=True)
    scope_errors = [
        f
        for f in audit.report.findings
        if f.article_id in ("office", "personal")
        and f.assessment.category.value in ("scope_fit", "practicality")
    ]
    assert not scope_errors, "Judge rejects supported non-coding use cases"
    print("PASS: scope, mild preference, quality, Judge", flush=True)


if __name__ == "__main__":
    main()
