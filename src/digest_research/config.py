"""Reading `ResearchBudget` from `config/config.json`."""

from __future__ import annotations

import json
from pathlib import Path

from digest_contracts import ResearchBudget

RESEARCH_KEY = "research"


def load_research_budget(path: Path | str) -> ResearchBudget:
    """The `research` block, validated against the contract's ranges. A missing block means the defaults."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ResearchBudget.model_validate(raw.get(RESEARCH_KEY, {}))
