"""Run the research loop against the real model on a fixed set of three articles.

This is not a test: it calls the model and costs money (about 0.1-0.3 USD at
list price). Run it by hand when the research prompt or the extraction schema
changes, and compare the printed table with the last run recorded in the PR.

    PYTHONPATH=tests .venv/bin/python evals/research_fixed_set.py

Pages the articles link to are served from memory, so nothing but the model is
contacted. Nothing is written to disk; the table goes to stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from research_support import (  # noqa: E402
    RESULTS_PAGE,
    RESULTS_URL,
    ScriptedWeb,
    article,
)

from agent_daily_digest.contracts.metrics import LLMCallMetrics
from agent_daily_digest.llm.client import invoke_structured
from agent_daily_digest.llm.pricing import load_pricing
from agent_daily_digest.research.evidence import render_packet
from agent_daily_digest.research.run import Researcher, ResearchInput

CASES = (
    ("complete in one round", ResearchInput(article("basic"))),
    (
        "primary source in round two",
        ResearchInput(
            article("links", fixture="article_links", title="Evaluating agent harnesses", url="https://example.com/posts/harness-eval"),
            links=(RESULTS_URL, "/posts/eval-method"),
        ),
    ),
    ("injected instructions", ResearchInput(article("inject", fixture="article_injection", title="Agent evaluation notes"))),
)


def main() -> None:
    config = ROOT / "config.json"
    calls: list[LLMCallMetrics] = []
    researcher = Researcher(
        invoke=invoke_structured,
        fetch=ScriptedWeb({RESULTS_URL: RESULTS_PAGE}),
        pricing=load_pricing(config),
        model="claude-sonnet-5",
        record=calls.append,
        max_call_usd=0.3,
    )
    for label, case in CASES:
        (packet,) = researcher.run([case]).packets
        print(f"## {label}: {packet.status.value} / {packet.stop_reason.value} / rounds {packet.trace.rounds}")
        print(render_packet(packet))
        print()
    total = sum(call.total_cost_usd or 0 for call in calls)
    print("| call | attempts | input tokens | output tokens | cost USD | errors |")
    print("|---|---|---|---|---|---|")
    for call in calls:
        errors = ", ".join(a.error.kind.value for a in call.attempts if a.error) or "-"
        print(f"| {call.call_id} | {len(call.attempts)} | {call.total_input_tokens} | {call.total_output_tokens} | {call.total_cost_usd} | {errors} |")
    print(f"\ntotal estimated cost: {total:.4f} USD")


if __name__ == "__main__":
    main()
