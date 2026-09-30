"""Cost is computed from `config.json`, so it can be checked by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_daily_digest.contracts.metrics import CostBasis
from agent_daily_digest.llm.pricing import (
    ModelUsage,
    estimate_cost,
    load_pricing,
    token_usage,
)

CONFIG = Path(__file__).resolve().parents[1] / "config.json"


@pytest.fixture(scope="module")
def table():
    return load_pricing(CONFIG)


# Hand-computed from the config's per-million prices. The Sonnet row is the
# Selector call the #2 spike measured: 4 input, 25,440 cache write, 20,084
# cache read. The Haiku row is the unrequested model the CLI also used.
@pytest.mark.parametrize(
    ("usage", "expected_usd"),
    [
        (
            ModelUsage("claude-sonnet-5", input_tokens=4, output_tokens=900, cache_creation_input_tokens=25_440, cache_read_input_tokens=20_084),
            (4 * 2.0 + 900 * 10.0 + 25_440 * 2.5 + 20_084 * 0.2) / 1_000_000,
        ),
        (
            ModelUsage("claude-haiku-4-5-20251001", input_tokens=1_067, output_tokens=13),
            (1_067 * 1.0 + 13 * 5.0) / 1_000_000,
        ),
        (
            ModelUsage("provider-alias", input_tokens=1_000_000, canonical_model="claude-haiku-4-5"),
            1.0,
        ),
    ],
)
def test_cost_per_model_matches_the_config_prices(table, usage, expected_usd) -> None:
    cost = estimate_cost((usage,), table)
    assert cost is not None
    assert cost.usd == pytest.approx(expected_usd, abs=1e-6)
    assert cost.basis is CostBasis.ESTIMATED


def test_an_attempt_that_used_two_models_sums_them(table) -> None:
    sonnet = ModelUsage("claude-sonnet-5", output_tokens=1_000)
    haiku = ModelUsage("claude-haiku-4-5-20251001", output_tokens=1_000)
    assert estimate_cost((sonnet, haiku), table).usd == pytest.approx(0.01 + 0.005)


def test_the_config_prices_every_model_the_pipeline_is_configured_to_call(table) -> None:
    import json

    models = json.loads(CONFIG.read_text(encoding="utf-8"))["models"]
    for role in ("research", "selector", "judge"):
        assert table.price_for(models[role]) is not None, role


def test_no_usage_means_no_cost_and_no_tokens(table) -> None:
    assert estimate_cost((), table) is None
    assert token_usage(()) is None


def test_a_config_without_a_price_block_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"models": {"selector": "claude-sonnet-5"}}', encoding="utf-8")
    with pytest.raises(KeyError):
        load_pricing(path)
