"""The deterministic gate chain from a collected item to a normalized article."""

from __future__ import annotations

import datetime as dt

import pytest

from digest_contracts import (
    GATE_ORDER,
    BodySource,
    ErrorKind,
    GateExclusionReason,
    GateName,
    ProcessedState,
    compute_content_hash,
)
from digest_normalize import (
    MIN_PRIMARY_INFO_CHARS,
    FetchFailure,
    FetchedPage,
    ProcessedIndex,
    extract_document,
    normalize_item,
    normalize_items,
)
from normalize_helpers import FEED_URL, StubFetcher, collected, feed_summary, page, processed_record

JST = dt.timezone(dt.timedelta(hours=9))


def basic_fetch() -> StubFetcher:
    return StubFetcher({FEED_URL: page("article_basic")})


def only_reason(result) -> GateExclusionReason:
    assert len(result.outcome.exclusion_reasons) == 1
    return result.outcome.exclusion_reasons[0]


class TestPassingArticle:
    @pytest.fixture()
    def result(self):
        return normalize_item(collected(), fetch=basic_fetch())

    def test_every_gate_passed(self, result) -> None:
        assert result.outcome.passed
        assert tuple(r.gate for r in result.outcome.results) == GATE_ORDER
        assert result.outcome.exclusion_reasons == ()

    def test_the_article_is_built_from_the_page(self, result) -> None:
        article = result.article
        assert article is not None
        assert article.article_id == "a001"
        assert article.canonical_url == "https://example.com/posts/retry-budget"
        assert article.title == "Synthetic title"
        assert article.author == "Synthetic Author"
        assert article.body_source is BodySource.EXTRACTED
        assert article.content_hash == compute_content_hash(article.body_text)

    def test_the_published_date_comes_from_the_collected_item(self, result) -> None:
        # `required_fields` runs before the fetch, so the feed value is the only
        # one available when the gate decides. The page value is reported separately.
        assert result.article is not None
        assert result.article.published_at == dt.datetime(2026, 9, 17, 6, 0, tzinfo=JST)
        assert result.extracted is not None
        assert result.extracted.published_at_raw == "2026-09-16T22:15:00+09:00"

    def test_the_fetcher_is_called_with_the_canonical_url(self) -> None:
        fetch = basic_fetch()
        normalize_item(collected(url="HTTPS://Example.com/posts/retry-budget?utm_source=rss#x"), fetch=fetch)
        assert fetch.requested == [FEED_URL]

    def test_no_failure_is_recorded(self, result) -> None:
        assert result.failure is None


class TestRequiredFields:
    @pytest.mark.parametrize("url", [None, "", "   ", "\u3000"])
    def test_a_blank_url_is_missing(self, url: str | None) -> None:
        result = normalize_item(collected(url=url), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.MISSING_URL

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "/posts/1", "http://user:pw@example.com/a"])
    def test_an_unusable_url_is_invalid(self, url: str) -> None:
        result = normalize_item(collected(url=url), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.INVALID_URL

    @pytest.mark.parametrize("title", [None, "", "  ", "\u200b"])
    def test_a_blank_title_is_missing(self, title: str | None) -> None:
        result = normalize_item(collected(title=title), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.MISSING_TITLE

    @pytest.mark.parametrize("value", [None, "", "  ", "\u3000"])
    def test_a_blank_published_at_is_missing_not_invalid(self, value: str | None) -> None:
        # `src/fetch.py` writes `""` when a feed carries no date.
        result = normalize_item(collected(published_at=value), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.MISSING_PUBLISHED_AT

    @pytest.mark.parametrize("value", ["yesterday", "2026-13-01T00:00:00Z", "17/09/2026"])
    def test_an_unparseable_published_at_is_invalid(self, value: str) -> None:
        result = normalize_item(collected(published_at=value), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.INVALID_PUBLISHED_AT

    def test_the_url_is_reported_before_the_title_and_the_date(self) -> None:
        result = normalize_item(collected(url=None, title=None, published_at=None), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.MISSING_URL

    def test_the_title_is_reported_before_the_date(self) -> None:
        result = normalize_item(collected(title=None, published_at=None), fetch=basic_fetch())
        assert only_reason(result) is GateExclusionReason.MISSING_TITLE

    def test_nothing_is_fetched_when_the_fields_are_unusable(self) -> None:
        fetch = basic_fetch()
        normalize_item(collected(url=None), fetch=fetch)
        assert fetch.requested == []

    def test_the_title_is_normalized(self) -> None:
        result = normalize_item(collected(title="  Retry\u3000budget \n"), fetch=basic_fetch())
        assert result.article is not None
        assert result.article.title == "Retry budget"


class TestContentAvailable:
    def failing_fetch(self, kind: ErrorKind = ErrorKind.NETWORK) -> StubFetcher:
        return StubFetcher({FEED_URL: FetchFailure(kind=kind, detail="connection reset")})

    def test_a_fetch_failure_without_enough_feed_information_is_excluded(self) -> None:
        result = normalize_item(collected(), fetch=self.failing_fetch())
        assert only_reason(result) is GateExclusionReason.BODY_FETCH_FAILED
        assert result.article is None

    def test_the_fetch_failure_is_reported_as_a_machine_readable_record(self) -> None:
        result = normalize_item(collected(), fetch=self.failing_fetch(ErrorKind.TIMEOUT))
        assert result.failure is not None
        assert result.failure.kind is ErrorKind.TIMEOUT

    def test_a_fetch_failure_with_enough_feed_information_falls_back(self) -> None:
        result = normalize_item(
            collected(feed_summary=feed_summary(MIN_PRIMARY_INFO_CHARS)), fetch=self.failing_fetch()
        )
        assert result.outcome.passed
        assert result.article is not None
        assert result.article.body_source is BodySource.FEED_FALLBACK
        assert result.article.body_text.startswith("The post reports a measured change")
        assert result.extracted is None

    def test_one_character_below_the_threshold_is_not_enough(self) -> None:
        result = normalize_item(
            collected(feed_summary=feed_summary(MIN_PRIMARY_INFO_CHARS - 1)), fetch=self.failing_fetch()
        )
        assert only_reason(result) is GateExclusionReason.BODY_FETCH_FAILED

    def test_a_thin_extraction_without_a_usable_summary_is_an_extraction_failure(self) -> None:
        result = normalize_item(collected(), fetch=StubFetcher({FEED_URL: page("article_thin")}))
        assert only_reason(result) is GateExclusionReason.BODY_EXTRACTION_FAILED

    def test_a_thin_extraction_falls_back_to_the_feed(self) -> None:
        result = normalize_item(
            collected(feed_summary=feed_summary(MIN_PRIMARY_INFO_CHARS + 50)),
            fetch=StubFetcher({FEED_URL: page("article_thin")}),
        )
        assert result.outcome.passed
        assert result.article is not None
        assert result.article.body_source is BodySource.FEED_FALLBACK

    def test_the_page_wins_when_both_carry_enough(self) -> None:
        result = normalize_item(
            collected(feed_summary=feed_summary(MIN_PRIMARY_INFO_CHARS * 3)), fetch=basic_fetch()
        )
        assert result.article is not None
        assert result.article.body_source is BodySource.EXTRACTED
        assert result.article.body_text.startswith("# Harness retry budget")

    def test_html_in_a_feed_summary_is_stripped(self) -> None:
        summary = "<p>" + feed_summary(MIN_PRIMARY_INFO_CHARS) + "</p><script>x()</script>"
        result = normalize_item(collected(feed_summary=summary), fetch=self.failing_fetch())
        assert result.article is not None
        assert "<p>" not in result.article.body_text
        assert "x()" not in result.article.body_text

    def test_a_blank_feed_summary_is_not_a_fallback(self) -> None:
        result = normalize_item(collected(feed_summary=None), fetch=self.failing_fetch())
        assert only_reason(result) is GateExclusionReason.BODY_FETCH_FAILED

    def test_a_redirect_changes_the_canonical_url(self) -> None:
        fetch = StubFetcher(
            {FEED_URL: page("article_no_wrapper", url=FEED_URL, final_url="https://example.com/posts/moved")}
        )
        result = normalize_item(collected(), fetch=fetch)
        assert result.article is not None
        assert result.article.canonical_url == "https://example.com/posts/moved"


class TestNotPreviouslyProcessed:
    def index_with(self, **overrides) -> ProcessedIndex:
        return ProcessedIndex.from_state(ProcessedState(records=(processed_record(**overrides),)))

    def test_a_known_url_is_excluded(self) -> None:
        index = self.index_with(canonical_url="https://example.com/posts/retry-budget", content_hash="a" * 64)
        result = normalize_item(collected(), fetch=basic_fetch(), index=index)
        assert only_reason(result) is GateExclusionReason.ALREADY_PROCESSED_URL

    def test_a_known_body_hash_under_another_url_is_excluded(self) -> None:
        body = extract_document(page("article_basic").html).body_text
        index = self.index_with(
            canonical_url="https://example.com/posts/elsewhere", content_hash=compute_content_hash(body)
        )
        result = normalize_item(collected(), fetch=basic_fetch(), index=index)
        assert only_reason(result) is GateExclusionReason.ALREADY_PROCESSED_CONTENT_HASH

    def test_the_url_reported_is_the_one_after_the_redirect(self) -> None:
        fetch = StubFetcher(
            {FEED_URL: page("article_no_wrapper", url=FEED_URL, final_url="https://example.com/posts/moved")}
        )
        index = self.index_with(canonical_url="https://example.com/posts/moved", content_hash="a" * 64)
        result = normalize_item(collected(), fetch=fetch, index=index)
        assert only_reason(result) is GateExclusionReason.ALREADY_PROCESSED_URL

    def test_the_url_requested_before_the_redirect_also_counts(self) -> None:
        fetch = StubFetcher(
            {FEED_URL: page("article_no_wrapper", url=FEED_URL, final_url="https://example.com/posts/moved")}
        )
        index = self.index_with(canonical_url=FEED_URL, content_hash="a" * 64)
        result = normalize_item(collected(), fetch=fetch, index=index)
        assert only_reason(result) is GateExclusionReason.ALREADY_PROCESSED_URL

    def test_an_unrelated_state_does_not_exclude(self) -> None:
        index = self.index_with(canonical_url="https://example.com/posts/other", content_hash="a" * 64)
        assert normalize_item(collected(), fetch=basic_fetch(), index=index).outcome.passed


class TestNotKnownDuplicate:
    def test_the_same_body_twice_in_one_run_keeps_the_first(self) -> None:
        fetch = StubFetcher(
            {
                FEED_URL: page("article_basic"),
                "https://example.com/posts/mirror": page("article_basic", url="https://example.com/posts/mirror"),
            }
        )
        results = normalize_items(
            (collected(), collected(article_id="a002", url="https://example.com/posts/mirror")),
            fetch=fetch,
        )
        assert results[0].outcome.passed
        assert only_reason(results[1]) is GateExclusionReason.DUPLICATE_OF_KNOWN_ARTICLE

    def test_the_same_canonical_url_twice_in_one_run_keeps_the_first(self) -> None:
        fetch = basic_fetch()
        results = normalize_items(
            (collected(), collected(article_id="a002", url=FEED_URL + "?utm_source=rss")), fetch=fetch
        )
        assert results[0].outcome.passed
        assert only_reason(results[1]) is GateExclusionReason.DUPLICATE_OF_KNOWN_ARTICLE

    def test_different_bodies_both_pass(self) -> None:
        fetch = StubFetcher(
            {
                FEED_URL: page("article_basic"),
                "https://example.com/posts/other": page(
                    "article_no_wrapper", url="https://example.com/posts/other"
                ),
            }
        )
        results = normalize_items(
            (collected(), collected(article_id="a002", url="https://example.com/posts/other")), fetch=fetch
        )
        assert all(r.outcome.passed for r in results)

    def test_an_excluded_article_does_not_block_a_later_one(self) -> None:
        fetch = StubFetcher(
            {
                FEED_URL: page("article_basic"),
                "https://example.com/posts/other": page(
                    "article_no_wrapper", url="https://example.com/posts/other"
                ),
            }
        )
        index = ProcessedIndex.from_state(
            ProcessedState(records=(processed_record(canonical_url=FEED_URL, content_hash="a" * 64),))
        )
        results = normalize_items(
            (collected(), collected(article_id="a002", url="https://example.com/posts/other")),
            fetch=fetch,
            index=index,
        )
        assert only_reason(results[0]) is GateExclusionReason.ALREADY_PROCESSED_URL
        assert results[1].outcome.passed

    def test_two_urls_that_declare_the_same_canonical_link_collapse(self) -> None:
        # `article_basic` names `https://example.com/posts/retry-budget` as its
        # canonical URL, so a syndicated copy resolves to the same article.
        fetch = StubFetcher(
            {
                FEED_URL: page("article_basic"),
                "https://example.com/syndicated/42": page(
                    "article_basic", url="https://example.com/syndicated/42"
                ),
            }
        )
        results = normalize_items(
            (collected(), collected(article_id="a002", url="https://example.com/syndicated/42")),
            fetch=fetch,
        )
        assert results[0].outcome.passed
        assert only_reason(results[1]) is GateExclusionReason.DUPLICATE_OF_KNOWN_ARTICLE

    def test_results_keep_the_input_order(self) -> None:
        fetch = basic_fetch()
        items = (collected(article_id="a003"), collected(article_id="a001", url=None))
        assert [r.article_id for r in normalize_items(items, fetch=fetch)] == ["a003", "a001"]


class TestGateOrder:
    @pytest.mark.parametrize(
        ("item_kwargs", "fetch_name", "expected_gates"),
        [
            ({"url": None}, "article_basic", 1),
            ({}, "article_thin", 2),
        ],
    )
    def test_evaluation_stops_right_after_the_first_failure(
        self, item_kwargs: dict, fetch_name: str, expected_gates: int
    ) -> None:
        fetch = StubFetcher({FEED_URL: page(fetch_name)})
        result = normalize_item(collected(**item_kwargs), fetch=fetch)
        gates = tuple(r.gate for r in result.outcome.results)
        assert gates == GATE_ORDER[:expected_gates]
        assert not result.outcome.results[-1].passed
        assert all(r.passed for r in result.outcome.results[:-1])

    def test_a_passing_article_carries_every_gate(self) -> None:
        result = normalize_item(collected(), fetch=basic_fetch())
        assert len(result.outcome.results) == len(GATE_ORDER)

    def test_the_gate_names_are_the_contract_names(self) -> None:
        result = normalize_item(collected(), fetch=basic_fetch())
        assert result.outcome.results[0].gate is GateName.REQUIRED_FIELDS
        assert result.outcome.results[-1].gate is GateName.NOT_KNOWN_DUPLICATE


class TestDeterminism:
    @pytest.mark.parametrize(
        ("kwargs", "fetch_name"),
        [
            ({}, "article_basic"),
            ({}, "article_injection"),
            ({}, "article_thin"),
            ({"published_at": "yesterday"}, "article_basic"),
        ],
    )
    def test_the_same_input_gives_the_same_result(self, kwargs: dict, fetch_name: str) -> None:
        def run() -> tuple[str, str | None]:
            result = normalize_item(collected(**kwargs), fetch=StubFetcher({FEED_URL: page(fetch_name)}))
            article = result.article.model_dump_json() if result.article else None
            return result.outcome.model_dump_json(), article

        assert run() == run()

    def test_the_hash_matches_the_stored_body(self) -> None:
        result = normalize_item(collected(), fetch=basic_fetch())
        assert result.article is not None
        assert result.article.content_hash == compute_content_hash(result.article.body_text)

    def test_a_body_that_differs_by_one_character_gets_a_different_hash(self) -> None:
        first = normalize_item(collected(), fetch=basic_fetch()).article
        altered = page("article_basic").html.replace("41%", "42%")
        second = normalize_item(
            collected(),
            fetch=StubFetcher(
                {
                    FEED_URL: FetchedPage(
                        requested_url=FEED_URL,
                        final_url=FEED_URL,
                        html=altered,
                        content_type="text/html; charset=utf-8",
                    )
                }
            ),
        ).article
        assert first is not None and second is not None
        assert first.content_hash != second.content_hash
