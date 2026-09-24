"""The gate chain from a collected item to a normalized article.

Gates run in `GATE_ORDER` and stop right after the first failure, which is one
of the two shapes `GateOutcome` accepts. Stopping is not only cheaper: a later
gate needs what an earlier one produced, and there is no honest answer for
`not_previously_processed` before a body hash exists.

That order also fixes where the publication date can come from. `required_fields`
decides before the page is fetched, so the collected item is the only source
available to it; the date a page declares is reported on `ExtractedDocument` and
is not used to build the article.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from digest_contracts import (
    GATE_ORDER,
    BodySource,
    CollectedItem,
    ErrorRecord,
    GateExclusionReason,
    GateName,
    GateOutcome,
    GateResult,
    NormalizedArticle,
    compute_content_hash,
)

from ._text import collapse_text, is_blank
from .dates import PublishedAtProblem, PublishedAtRejected, parse_published_at
from .extract import ExtractedDocument, extract_document
from .fetching import BodyFetcher, FetchedPage
from .state import ProcessedIndex
from .urls import UrlProblem, UrlRejected, canonicalize_url, same_site

MIN_PRIMARY_INFO_CHARS = 200
"""How much text counts as primary information.

The same bar applies to an extracted body and to a feed summary: below it,
neither tells a reader what the article did, so there is no reason to accept one
and reject the other. `digest_collect` caps a feed summary at 400 characters
(`SUMMARY_MAX_CHARS`), which puts the bar at half of what a feed can carry.
"""


@dataclass(frozen=True)
class NormalizationResult:
    """What one collected item produced: the gate record, and the article if it passed.

    `failure` is the fetch failure, if there was one. It is also set on a result
    that passed, because an article can pass on the feed's primary information
    after the page could not be read, and #9 records why the body came from there.
    `extracted` is the page as it was read; it stays in memory for the run and is
    never written to the processing state.
    """

    article_id: str
    outcome: GateOutcome
    article: NormalizedArticle | None = None
    extracted: ExtractedDocument | None = None
    failure: ErrorRecord | None = None

    @property
    def passed(self) -> bool:
        return self.outcome.passed

    @property
    def exclusion_reason(self) -> GateExclusionReason | None:
        reasons = self.outcome.exclusion_reasons
        return reasons[0] if reasons else None


@dataclass
class RunSeen:
    """Canonical URLs and body hashes accepted earlier in the same run."""

    urls: set[str] = field(default_factory=set)
    content_hashes: set[str] = field(default_factory=set)

    def matches(self, urls: tuple[str, ...], content_hash: str) -> bool:
        return content_hash in self.content_hashes or any(url in self.urls for url in urls)

    def add(self, urls: tuple[str, ...], content_hash: str) -> None:
        self.urls.update(urls)
        self.content_hashes.add(content_hash)


def normalize_items(
    items: tuple[CollectedItem, ...],
    *,
    fetch: BodyFetcher,
    index: ProcessedIndex | None = None,
) -> tuple[NormalizationResult, ...]:
    """Normalize a run's items in order, excluding repeats of what already passed."""
    seen = RunSeen()
    return tuple(normalize_item(item, fetch=fetch, index=index, seen=seen) for item in items)


def normalize_item(
    item: CollectedItem,
    *,
    fetch: BodyFetcher,
    index: ProcessedIndex | None = None,
    seen: RunSeen | None = None,
) -> NormalizationResult:
    """Run every gate for one item and build the article when all of them pass."""
    index = index or ProcessedIndex.empty()
    passed: list[GateResult] = []

    # 1. required_fields -------------------------------------------------
    try:
        requested_url = canonicalize_url(item.url)
    except UrlRejected as rejected:
        reason = (
            GateExclusionReason.MISSING_URL
            if rejected.problem is UrlProblem.MISSING
            else GateExclusionReason.INVALID_URL
        )
        return _excluded(item, passed, reason)
    if is_blank(item.title):
        return _excluded(item, passed, GateExclusionReason.MISSING_TITLE)
    try:
        published_at = parse_published_at(item.published_at)
    except PublishedAtRejected as rejected:
        return _excluded(item, passed, _PUBLISHED_AT_REASONS[rejected.problem])
    passed.append(_passed(GateName.REQUIRED_FIELDS))

    # 2. content_available -----------------------------------------------
    fetched = fetch(requested_url)
    failure = None if isinstance(fetched, FetchedPage) else fetched.to_error_record()
    if isinstance(fetched, FetchedPage):
        extracted: ExtractedDocument | None = extract_document(fetched.html)
        canonical_url = _canonical_url(fetched, requested_url, extracted)
    else:
        extracted, canonical_url = None, requested_url

    body_text, body_source = _body(extracted, item)
    if body_text is None:
        reason = (
            GateExclusionReason.BODY_EXTRACTION_FAILED
            if extracted is not None
            else GateExclusionReason.BODY_FETCH_FAILED
        )
        return _excluded(item, passed, reason, extracted=extracted, failure=failure)
    content_hash = compute_content_hash(body_text)
    passed.append(_passed(GateName.CONTENT_AVAILABLE))

    # 3. not_previously_processed ----------------------------------------
    urls = (canonical_url,) if canonical_url == requested_url else (canonical_url, requested_url)
    if any(index.has_url(url) for url in urls):
        return _excluded(
            item, passed, GateExclusionReason.ALREADY_PROCESSED_URL, extracted=extracted, failure=failure
        )
    if index.has_content_hash(content_hash):
        return _excluded(
            item,
            passed,
            GateExclusionReason.ALREADY_PROCESSED_CONTENT_HASH,
            extracted=extracted,
            failure=failure,
        )
    passed.append(_passed(GateName.NOT_PREVIOUSLY_PROCESSED))

    # 4. not_known_duplicate ---------------------------------------------
    # Only an exact repeat of a body or a URL already accepted in this run. The
    # semantic judgement of whether two articles say the same thing is #6's.
    if seen is not None and seen.matches(urls, content_hash):
        return _excluded(
            item,
            passed,
            GateExclusionReason.DUPLICATE_OF_KNOWN_ARTICLE,
            extracted=extracted,
            failure=failure,
        )
    passed.append(_passed(GateName.NOT_KNOWN_DUPLICATE))
    if seen is not None:
        seen.add(urls, content_hash)

    article = NormalizedArticle(
        article_id=item.article_id,
        source_id=item.source_id,
        source_kind=item.source_kind,
        canonical_url=canonical_url,
        title=collapse_text(item.title or ""),
        author=extracted.author if extracted else None,
        published_at=published_at,
        body_source=body_source,
        body_text=body_text,
        content_hash=content_hash,
    )
    return NormalizationResult(
        article_id=item.article_id,
        outcome=GateOutcome(article_id=item.article_id, results=tuple(passed)),
        article=article,
        extracted=extracted,
        failure=failure,
    )


_PUBLISHED_AT_REASONS = {
    PublishedAtProblem.MISSING: GateExclusionReason.MISSING_PUBLISHED_AT,
    PublishedAtProblem.UNPARSEABLE: GateExclusionReason.INVALID_PUBLISHED_AT,
}


def _passed(gate: GateName) -> GateResult:
    return GateResult(gate=gate, passed=True)


def _excluded(
    item: CollectedItem,
    passed: list[GateResult],
    reason: GateExclusionReason,
    *,
    extracted: ExtractedDocument | None = None,
    failure: ErrorRecord | None = None,
) -> NormalizationResult:
    gate = GATE_ORDER[len(passed)]
    results = (*passed, GateResult(gate=gate, passed=False, reason=reason))
    return NormalizationResult(
        article_id=item.article_id,
        outcome=GateOutcome(article_id=item.article_id, results=results),
        extracted=extracted,
        failure=failure,
    )


def _body(
    extracted: ExtractedDocument | None, item: CollectedItem
) -> tuple[str, BodySource] | tuple[None, None]:
    """The body to keep: the page when it carries enough, the feed otherwise."""
    if extracted is not None and len(extracted.body_text) >= MIN_PRIMARY_INFO_CHARS:
        return extracted.body_text, BodySource.EXTRACTED
    fallback = _feed_fallback(item)
    if fallback is not None:
        return fallback, BodySource.FEED_FALLBACK
    return None, None


def _feed_fallback(item: CollectedItem) -> str | None:
    if is_blank(item.feed_summary):
        return None
    # A feed summary may carry markup, so it goes through the same extraction.
    text = extract_document(item.feed_summary or "").body_text
    return text if len(text) >= MIN_PRIMARY_INFO_CHARS else None


def _canonical_url(page: FetchedPage, requested_url: str, extracted: ExtractedDocument) -> str:
    """Prefer the URL the page declares, but only when it cannot change identity.

    A page that names another site as its canonical URL would otherwise decide
    what the digest records and dedupes against, so such a link is ignored.
    """
    base = requested_url
    if page.final_url and page.final_url != page.requested_url:
        try:
            base = canonicalize_url(page.final_url)
        except UrlRejected:
            base = requested_url
    if not extracted.canonical_url_raw:
        return base
    try:
        declared = canonicalize_url(extracted.canonical_url_raw)
    except UrlRejected:
        return base
    return declared if same_site(declared, base) or same_site(declared, requested_url) else base
