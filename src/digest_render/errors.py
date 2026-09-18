"""Failures the renderer reports to its caller.

Every one is identifiable: the pipeline (#10) catches `RenderError` to keep a
run from publishing, and branches on the subclass to record why.
"""

from __future__ import annotations

from digest_contracts import Tier

from .forbidden import ForbiddenArtifact


class RenderError(Exception):
    """Base for every renderer failure. Nothing is written when one is raised."""


class MissingArticleError(RenderError):
    """An included article has no `NormalizedArticle`.

    The renderer does not guess a title or a URL. Supplying the metadata for
    every included article is the caller's responsibility.
    """

    def __init__(self, article_ids: tuple[str, ...]) -> None:
        self.article_ids = article_ids
        super().__init__("no article metadata for: " + ", ".join(article_ids))


class TierCapError(RenderError):
    """A tier holds more entries than the Design Doc allows.

    `SelectorOutput` already rejects this, so it can only arrive through
    `model_construct`. The renderer re-checks so an unvalidated object cannot
    become a published digest.
    """

    def __init__(self, tier: Tier, count: int, cap: int) -> None:
        self.tier = tier
        self.count = count
        self.cap = cap
        super().__init__(f"{tier.value} holds {count} entries, the cap is {cap}")


class ForbiddenArtifactError(RenderError):
    """The digest text contains banned sentence-level artifacts.

    Raised instead of returned because a digest that fails this check must not
    be published (Design Doc, Failure policy). `findings` carries every match,
    so the caller can record them without re-running the check.
    """

    def __init__(self, findings: tuple[ForbiddenArtifact, ...]) -> None:
        self.findings = findings
        super().__init__(
            f"{len(findings)} forbidden artifact(s): "
            + "; ".join(str(finding) for finding in findings)
        )


class IndexMarkerError(RenderError):
    """`digests/README.md` lacks the index markers, or holds them out of order."""
