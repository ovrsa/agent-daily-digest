"""Valid baseline payloads for contract tests.

Each factory returns a plain dict that validates as-is. Tests copy it and
change one field to exercise a single rule. Texts are synthetic; no real
article body or model output is stored here.
"""

from __future__ import annotations

import hashlib
from typing import Any

T0 = "2026-09-17T06:00:00+09:00"
T1 = "2026-09-17T06:00:05+09:00"
T2 = "2026-09-17T06:00:09+09:00"

BODY = "Synthetic normalized body used only by contract tests."
BODY_HASH = hashlib.sha256(BODY.encode("utf-8")).hexdigest()


def collected_item(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "article_id": "a001",
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "title": "Synthetic title",
        "url": "https://example.com/posts/1",
        "published_at": T0,
        "feed_summary": "Synthetic feed summary.",
    }
    data.update(overrides)
    return data


def source_fetch_result(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "status": "succeeded",
        "items": [collected_item()],
        "duration_ms": 1200,
        "failure": None,
    }
    data.update(overrides)
    return data


def normalized_article(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "article_id": "a001",
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "canonical_url": "https://example.com/posts/1",
        "title": "Synthetic title",
        "author": "Synthetic Author",
        "published_at": T0,
        "body_source": "extracted",
        "body_text": BODY,
        "content_hash": BODY_HASH,
    }
    data.update(overrides)
    return data


def processed_record(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "canonical_url": "https://example.com/posts/1",
        "first_seen_at": T0,
        "content_hash": BODY_HASH,
        "last_decision": "included",
    }
    data.update(overrides)
    return data


def passed_gate_results() -> list[dict[str, Any]]:
    return [
        {"gate": "required_fields", "passed": True, "reason": None},
        {"gate": "content_available", "passed": True, "reason": None},
        {"gate": "not_previously_processed", "passed": True, "reason": None},
        {"gate": "not_known_duplicate", "passed": True, "reason": None},
    ]


def gate_outcome(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"article_id": "a001", "results": passed_gate_results()}
    data.update(overrides)
    return data


def axis_scores(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "practicality": 4,
        "specificity_reproducibility": 5,
        "novelty": 3,
        "source_reliability": 4,
        "reader_impact": 4,
        "read_original_value": 5,
    }
    data.update(overrides)
    return data


def error_record(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"kind": "timeout", "detail": "request exceeded 20s"}
    data.update(overrides)
    return data


def stage(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "stage": "collect",
        "status": "succeeded",
        "started_at": T0,
        "ended_at": T1,
        "error": None,
    }
    data.update(overrides)
    return data


def llm_attempt(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "attempt_number": 1,
        "status": "succeeded",
        "started_at": T0,
        "ended_at": T1,
        "usage": {"input_tokens": 12000, "output_tokens": 1800},
        "cost": {"usd": 0.063, "basis": "estimated"},
        "error": None,
        "validation_issues": [],
    }
    data.update(overrides)
    return data


def llm_call(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "call_id": "selector-1",
        "role": "selector",
        "model": "claude-sonnet-5",
        "prompt_version": "selector-v1",
        "attempts": [llm_attempt()],
    }
    data.update(overrides)
    return data


def source_metrics(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_id": "simonw",
        "source_kind": "fixed_watch",
        "status": "succeeded",
        "item_count": 1,
        "duration_ms": 1200,
        "failure": None,
    }
    data.update(overrides)
    return data


def article_metrics(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "article_id": "a001",
        "source_id": "simonw",
        "canonical_url": "https://example.com/posts/1",
        "body_source": "extracted",
        "char_count": len(BODY),
        "gate": gate_outcome(),
        "scores": axis_scores(),
        "decision": "included",
        "tier": "must_read",
        "decision_reason": "Shows a reproducible harness change with numbers.",
    }
    data.update(overrides)
    return data


ALL_STAGES = (
    "collect",
    "normalize",
    "gate",
    "research",
    "select",
    "render",
    "publish",
    "judge",
    "comment",
)


def run_metrics(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "run_id": "run-2026-09-17",
        "status": "succeeded",
        "started_at": T0,
        "ended_at": T2,
        "stages": [stage(stage=name) for name in ALL_STAGES],
        "sources": [source_metrics()],
        "articles": [article_metrics()],
        "llm_calls": [llm_call()],
        "judge_findings": [],
        "published_must_read_count": 1,
        "published_worth_knowing_count": 0,
    }
    data.update(overrides)
    return data
