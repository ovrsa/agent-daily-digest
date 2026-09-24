"""The model names a run uses, from `config/config.json`'s `models` block."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from digest_observe import MODELS_KEY


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
