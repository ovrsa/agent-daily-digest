"""Cost of an LLM call, computed from the price table in `config.json`.

The provider reports a cost of its own (`costUSD`), but that figure is a list
price computed inside the CLI and cannot be checked from this repository. The
cost recorded here is computed from the token counts and the `models.pricing`
block, so a reviewer can recompute it by hand. Both are list prices; a run
under subscription auth is not billed per token, so the basis is `estimated`.

Cache tokens are priced separately because they dominate: the #2 spike measured
`input_tokens=4` against `cache_creation_input_tokens=25,440` for one Selector
call, so pricing only input and output would understate the cost by orders of
magnitude.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_daily_digest.contracts import CostBasis, CostRecord, TokenUsage

MODELS_KEY = "models"
PRICING_KEY = "pricing_usd_per_mtok"

_DATE_SUFFIX = re.compile(r"-\d{8}$")


class ModelPrice(BaseModel):
    """List price in USD per million tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_write: float = Field(ge=0)
    """5-minute cache write."""
    cache_read: float = Field(ge=0)


@dataclass(frozen=True)
class ModelUsage:
    """Tokens one model consumed during one attempt.

    An attempt can involve more than the requested model: the #2 spike saw the
    CLI spend tokens on `claude-haiku-4-5-20251001` next to the requested
    Sonnet, so usage is kept per model and priced per model.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    canonical_model: str | None = None

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


class PricingTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prices: Mapping[str, ModelPrice]

    def price_for(self, model: str, canonical_model: str | None = None) -> ModelPrice | None:
        """The price of `model`, trying its canonical id and its undated id next."""
        for key in (model, canonical_model, _DATE_SUFFIX.sub("", model)):
            if key and key in self.prices:
                return self.prices[key]
        return None


def load_pricing(path: Path | str) -> PricingTable:
    """Read `models.pricing_usd_per_mtok` from a config file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    block = raw.get(MODELS_KEY, {}).get(PRICING_KEY)
    if block is None:
        raise KeyError(f"{path} has no {MODELS_KEY}.{PRICING_KEY} block")
    return PricingTable.model_validate({"prices": block})


def token_usage(usages: Iterable[ModelUsage]) -> TokenUsage | None:
    """Tokens of one attempt, summed over every model it used.

    `TokenUsage.input_tokens` counts every input token the models processed,
    cached or not. The contract has no cache fields, and leaving cache tokens
    out would make a call that read 45,000 tokens look like it read 4.
    """
    usages = tuple(usages)
    if not usages:
        return None
    return TokenUsage(
        input_tokens=sum(u.total_input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
    )


def estimate_cost(usages: Iterable[ModelUsage], table: PricingTable) -> CostRecord | None:
    """Cost of one attempt at list price, or `None` when a model has no price.

    A model missing from the table makes the whole attempt unpriced rather than
    silently cheaper: a partial sum would look like a real figure.
    """
    usages = tuple(usages)
    if not usages:
        return None
    total = 0.0
    for usage in usages:
        price = table.price_for(usage.model, usage.canonical_model)
        if price is None:
            return None
        total += (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_creation_input_tokens * price.cache_write
            + usage.cache_read_input_tokens * price.cache_read
        ) / 1_000_000
    return CostRecord(usd=round(total, 6), basis=CostBasis.ESTIMATED)
