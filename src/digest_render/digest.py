"""Assemble one daily digest from the Selector's structured output.

The renderer is mechanical. It does not choose, rank, shorten or reword
anything: the wording comes from `DigestEntry`, the running order comes from
`SelectorOutput`, and the article metadata comes from `NormalizedArticle`. No
model is called here, which is what makes the same input produce the same bytes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date

from digest_contracts import (
    MUST_READ_MAX,
    WORTH_KNOWING_MAX,
    IncludedArticle,
    NormalizedArticle,
    SelectorOutput,
    Tier,
)

from .errors import ForbiddenArtifactError, MissingArticleError, TierCapError
from .forbidden import ForbiddenArtifact, check_forbidden_artifacts
from .markdown import inline, link

DIGEST_TYPE = "agent-daily-digest"
DIGEST_TITLE = "Agent / Coding Agent Daily Digest"

_TIERS: tuple[tuple[Tier, str, int], ...] = (
    (Tier.MUST_READ, "Must Read", MUST_READ_MAX),
    (Tier.WORTH_KNOWING, "Worth Knowing", WORTH_KNOWING_MAX),
)

# Wording fixed by the Design Doc's Output contract.
_WHAT_HAPPENED = "何をしたか／何が分かったか"
_WHY_READ = "読む理由"
_EVIDENCE = "根拠"
_CAVEAT = "留保"


def digest_filename(digest_date: date) -> str:
    """`digests/` keeps one file per day, named by date so the index can sort it."""
    return f"{digest_date.isoformat()}.md"


def render_digest(
    selector_output: SelectorOutput,
    articles: Mapping[str, NormalizedArticle],
    digest_date: date,
) -> str | None:
    """Return the digest Markdown, or `None` when nothing was adopted.

    Adopting nothing is a normal outcome (Design Doc, Failure policy), so the
    caller writes no file rather than an empty digest.

    Raises `MissingArticleError` when an included article has no metadata,
    `TierCapError` when a tier exceeds its cap, and `ForbiddenArtifactError`
    when the Selector-authored text contains a banned artifact. Each is raised
    before any text is returned, so a rejected digest is never published.
    """
    if not selector_output.included:
        return None
    _check_caps(selector_output)
    _check_articles_present(selector_output, articles)
    _check_forbidden(selector_output)

    lines = [
        "---",
        f"date: {digest_date.isoformat()}",
        f"type: {DIGEST_TYPE}",
        "---",
        "",
        f"# {DIGEST_TITLE} {digest_date.isoformat()}",
    ]
    for tier, heading, _cap in _TIERS:
        bucket = _bucket(selector_output, tier)
        if not bucket:
            continue
        lines += ["", f"## {heading}"]
        for included in bucket:
            lines += _entry_lines(included, articles[included.article_id])
    return "\n".join(lines) + "\n"


def forbidden_artifacts_in(selector_output: SelectorOutput) -> tuple[ForbiddenArtifact, ...]:
    """Every banned artifact the digest text would carry, without rendering it."""
    return tuple(
        artifact
        for location, text in _checked_spans(selector_output)
        for artifact in check_forbidden_artifacts(text, location=location)
    )


def _bucket(selector_output: SelectorOutput, tier: Tier) -> tuple[IncludedArticle, ...]:
    return selector_output.must_read if tier is Tier.MUST_READ else selector_output.worth_knowing


def _entry_lines(included: IncludedArticle, article: NormalizedArticle) -> list[str]:
    entry = included.entry
    lines = [
        "",
        f"### {link(article.title, article.canonical_url)}",
        "",
        _meta_line(article),
        "",
        f"- {_WHAT_HAPPENED}: {inline(entry.what_happened.text)}",
        f"- {_WHY_READ}: {inline(entry.why_read)}",
        f"- {_EVIDENCE}: {inline(entry.evidence.text)}",
    ]
    if entry.caveat is not None:
        lines.append(f"- {_CAVEAT}: {inline(entry.caveat.text)}")
    return lines


def _meta_line(article: NormalizedArticle) -> str:
    # `published_at` is rendered in the offset it carries. Converting to one
    # zone would be an editorial rule the contract does not state.
    parts = [f"ソース: {article.source_id}"]
    if article.author is not None:
        parts.append(f"著者: {inline(article.author)}")
    parts.append(f"公開: {article.published_at.date().isoformat()}")
    return " / ".join(parts)


def _check_caps(selector_output: SelectorOutput) -> None:
    for tier, _heading, cap in _TIERS:
        count = len(_bucket(selector_output, tier))
        if count > cap:
            raise TierCapError(tier, count, cap)


def _check_articles_present(
    selector_output: SelectorOutput, articles: Mapping[str, NormalizedArticle]
) -> None:
    missing = tuple(a.article_id for a in selector_output.included if a.article_id not in articles)
    if missing:
        raise MissingArticleError(missing)


def _check_forbidden(selector_output: SelectorOutput) -> None:
    findings = forbidden_artifacts_in(selector_output)
    if findings:
        raise ForbiddenArtifactError(findings)


def _checked_spans(selector_output: SelectorOutput) -> Iterator[tuple[str, str]]:
    """The Selector-authored text, paired with a location a caller can act on.

    Titles and URLs are excluded on purpose: they are quoted source text, and
    an em dash in an original English title is the publisher's, not ours.
    """
    for tier, _heading, _cap in _TIERS:
        for index, included in enumerate(_bucket(selector_output, tier)):
            entry = included.entry
            at = f"{tier.value}[{index}].entry"
            yield f"{at}.what_happened.text", entry.what_happened.text
            yield f"{at}.why_read", entry.why_read
            yield f"{at}.evidence.text", entry.evidence.text
            if entry.caveat is not None:
                yield f"{at}.caveat.text", entry.caveat.text

