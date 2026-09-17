"""Selector evaluation vocabulary: axis scores, decision and tier."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field

from ._base import ContractModel

MUST_READ_MAX = 5
WORTH_KNOWING_MAX = 8

AxisScore = Annotated[int, Field(ge=1, le=5, strict=True)]


class Decision(str, Enum):
    """採否."""

    INCLUDED = "included"
    EXCLUDED = "excluded"


class Tier(str, Enum):
    """区分 of an included article."""

    MUST_READ = "must_read"
    WORTH_KNOWING = "worth_knowing"


class AxisScores(ContractModel):
    """Six-axis 1-5 evaluation. The contract does not define a total score."""

    practicality: AxisScore = Field(description="実用性")
    specificity_reproducibility: AxisScore = Field(description="具体性と再現性")
    novelty: AxisScore = Field(description="新規性")
    source_reliability: AxisScore = Field(description="情報源の信頼性")
    reader_impact: AxisScore = Field(description="読者への影響度")
    read_original_value: AxisScore = Field(description="原文を読む価値")
