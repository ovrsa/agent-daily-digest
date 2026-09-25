"""What a run reads from `config.json` besides the stages' own blocks.

- `models`: the model names for research, the Selector and the Judge
- `run`: `max_articles`, how many articles past the gates one run researches
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent_daily_digest.contracts import ResearchBudget
from agent_daily_digest.observe import MODELS_KEY

RUN_KEY = "run"
RESEARCH_KEY = "research"

DEFAULT_MAX_ARTICLES = 20
"""The research cap when the config has no `run` block.

The Selector decides every researched article in one call, bounded by
`Selector.max_call_usd` and the call's timeout. On 2026-09-25 five articles took
45 seconds of that call, so a run above roughly 30 would not finish, and a
failed selection publishes nothing and leaves the processing state as it was:
the next run would research the same articles again and fail the same way.
The same day, 58 articles of the seven-day window passed the gates, 23 of them
from the last 24 hours; on a day above the cap the rest wait a day.
"""


@dataclass(frozen=True)
class Models:
    research: str
    selector: str
    judge: str


def load_models(path: Path | str) -> Models:
    """Read `models.research`, `models.selector` and `models.judge`. A missing name is an error."""
    block = json.loads(Path(path).read_text(encoding="utf-8")).get(MODELS_KEY, {})
    missing = [role for role in ("research", "selector", "judge") if not isinstance(block.get(role), str) or not block[role]]
    if missing:
        raise KeyError(f"{path} has no {MODELS_KEY}.{', '.join(missing)}")
    return Models(research=block["research"], selector=block["selector"], judge=block["judge"])


def load_max_articles(path: Path | str) -> int:
    """Read `run.max_articles`, a positive integer. No `run` block means `DEFAULT_MAX_ARTICLES`.

    Anything else in the block is an error, so a misspelt key does not quietly
    fall back to the default.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if RUN_KEY not in raw:
        return DEFAULT_MAX_ARTICLES
    block = raw[RUN_KEY]
    if not isinstance(block, dict) or set(block) != {"max_articles"}:
        raise ValueError(f"{path}: {RUN_KEY} must be an object with max_articles only")
    value = block["max_articles"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{path}: {RUN_KEY}.max_articles must be a positive integer")
    return value


def load_research_budget(path: Path | str) -> ResearchBudget:
    """Read and validate the research budget, using contract defaults when absent."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ResearchBudget.model_validate(raw.get(RESEARCH_KEY, {}))
