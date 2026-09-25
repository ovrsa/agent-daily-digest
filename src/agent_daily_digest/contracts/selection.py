"""Selector structured output: six-axis scores, decision, tier, reason and entry text."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ._base import ArticleId, ContractModel, NonBlankStr, first_duplicate

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


EvidenceRef = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
"""Opaque Evidence ID. Its format and resolution to the source belong to #12."""


class _Statement(ContractModel):
    """A sentence of the 掲載文 and the Evidence IDs behind it."""

    text: NonBlankStr
    evidence_ids: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def _check_unique(self) -> _Statement:
        if first_duplicate(self.evidence_ids) is not None:
            raise ValueError("evidence_ids must be unique")
        return self


class FactStatement(_Statement):
    """A factual sentence and the Evidence IDs that support it."""

    evidence_ids: tuple[EvidenceRef, ...] = Field(min_length=1)


class CaveatStatement(_Statement):
    """A 留保 and the Evidence IDs behind it, when there are any.

    留保は本文に無いこと（未確認事項、測定条件の欠落）を書く場合があるので、
    対応する Evidence ID を持たないことがある。1件以上を求めると ID の捏造を招く。
    """

    evidence_ids: tuple[EvidenceRef, ...] = ()


class DigestEntry(ContractModel):
    """掲載文 of one included article, rendered to Markdown without further editing."""

    what_happened: FactStatement = Field(description="何をしたか／何が分かったか。本文で確認できる事実だけ")
    why_read: NonBlankStr = Field(description="読む理由。編集上の判断で、事実とは分ける")
    evidence: FactStatement = Field(description="根拠。コード、設定、数値、比較条件、失敗例など")
    caveat: CaveatStatement | None = Field(
        default=None,
        description="留保。本文の制約か、本文に書かれていない未確認事項。Evidence ID は無くてよい",
    )
    headline: NonBlankStr = Field(
        description="ダイジェスト冒頭の一覧に載せる1行の要点。記事が何の話かを書く。編集上の要約で、Evidence ID は持たない",
    )


class IncludedArticle(ContractModel):
    article_id: ArticleId
    scores: AxisScores
    decision_reason: NonBlankStr
    entry: DigestEntry


class ExcludedArticle(ContractModel):
    article_id: ArticleId
    scores: AxisScores
    decision_reason: NonBlankStr


class DuplicateGroup(ContractModel):
    """Articles carrying substantially the same information, and the one chosen to represent them."""

    representative_id: ArticleId
    duplicate_ids: tuple[ArticleId, ...] = Field(min_length=1)


class SelectorOutput(ContractModel):
    """Structured output of the Selector.

    The bucket an article sits in is its decision and tier, so the caps are
    `maxItems` in the JSON Schema. There is no minimum; adopting nothing is valid.
    """

    must_read: tuple[IncludedArticle, ...] = Field(max_length=MUST_READ_MAX)
    worth_knowing: tuple[IncludedArticle, ...] = Field(max_length=WORTH_KNOWING_MAX)
    excluded: tuple[ExcludedArticle, ...]
    duplicate_groups: tuple[DuplicateGroup, ...] = ()

    @property
    def included(self) -> tuple[IncludedArticle, ...]:
        return self.must_read + self.worth_knowing

    @property
    def article_ids(self) -> tuple[str, ...]:
        return tuple(a.article_id for a in (*self.must_read, *self.worth_knowing, *self.excluded))

    def decision_of(self, article_id: str) -> tuple[Decision, Tier | None]:
        for tier, bucket in ((Tier.MUST_READ, self.must_read), (Tier.WORTH_KNOWING, self.worth_knowing)):
            if any(a.article_id == article_id for a in bucket):
                return Decision.INCLUDED, tier
        if any(a.article_id == article_id for a in self.excluded):
            return Decision.EXCLUDED, None
        raise KeyError(article_id)

    @model_validator(mode="after")
    def _check_articles(self) -> SelectorOutput:
        if first_duplicate(self.article_ids) is not None:
            raise ValueError("an article must appear in exactly one of must_read, worth_knowing, excluded")
        known = set(self.article_ids)
        excluded = {a.article_id for a in self.excluded}
        grouped: set[str] = set()
        for group in self.duplicate_groups:
            members = (group.representative_id, *group.duplicate_ids)
            if first_duplicate(members) is not None:
                raise ValueError("a duplicate group lists an article twice")
            if not known.issuperset(members):
                raise ValueError("a duplicate group references an article that was not evaluated")
            if not grouped.isdisjoint(members):
                raise ValueError("an article belongs to at most one duplicate group")
            if not excluded.issuperset(group.duplicate_ids):
                raise ValueError("only the representative of a duplicate group may be included")
            grouped.update(members)
        return self
