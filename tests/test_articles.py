from __future__ import annotations

import pytest
from pydantic import ValidationError

import factories as f
from digest_contracts import (
    CollectedItem,
    NormalizedArticle,
    ProcessedRecord,
    ProcessedState,
    SourceFetchResult,
    compute_content_hash,
)


class TestCollectedItem:
    def test_valid_item(self) -> None:
        item = CollectedItem.model_validate(f.collected_item())
        assert item.source_kind.value == "fixed_watch"

    def test_raw_fields_may_be_missing_so_gates_can_record_the_reason(self) -> None:
        item = CollectedItem.model_validate(
            f.collected_item(title=None, url="not a url", published_at=None, feed_summary=None)
        )
        assert item.url == "not a url"

    @pytest.mark.parametrize("field", ["article_id", "source_id", "source_kind"])
    def test_identity_fields_are_required(self, field: str) -> None:
        data = f.collected_item()
        del data[field]
        with pytest.raises(ValidationError):
            CollectedItem.model_validate(data)

    @pytest.mark.parametrize("article_id", ["", "has space", "-leading", "x" * 65])
    def test_article_id_format(self, article_id: str) -> None:
        with pytest.raises(ValidationError):
            CollectedItem.model_validate(f.collected_item(article_id=article_id))

    def test_unknown_source_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CollectedItem.model_validate(f.collected_item(source_kind="survey"))

    @pytest.mark.parametrize(
        "published_at",
        [f.T0, "2026-09-17T06:00:00", "2026-09-17", "Wed, 17 Sep 2026 06:00:00 +0900", "not a date"],
    )
    def test_published_at_is_kept_raw_for_the_required_fields_gate(self, published_at: str) -> None:
        item = CollectedItem.model_validate(f.collected_item(published_at=published_at))
        assert item.published_at == published_at

    def test_extra_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CollectedItem.model_validate(f.collected_item(score=10))


class TestSourceFetchResult:
    def test_succeeded_with_items(self) -> None:
        result = SourceFetchResult.model_validate(f.source_fetch_result())
        assert result.item_count == 1

    def test_succeeded_with_zero_items(self) -> None:
        result = SourceFetchResult.model_validate(f.source_fetch_result(items=[]))
        assert result.item_count == 0

    def test_failed_requires_failure(self) -> None:
        with pytest.raises(ValidationError):
            SourceFetchResult.model_validate(f.source_fetch_result(status="failed", items=[]))

    def test_failed_must_not_carry_items(self) -> None:
        with pytest.raises(ValidationError):
            SourceFetchResult.model_validate(
                f.source_fetch_result(status="failed", failure=f.error_record())
            )

    def test_succeeded_must_not_carry_failure(self) -> None:
        with pytest.raises(ValidationError):
            SourceFetchResult.model_validate(f.source_fetch_result(failure=f.error_record()))

    def test_items_must_belong_to_the_source(self) -> None:
        with pytest.raises(ValidationError):
            SourceFetchResult.model_validate(
                f.source_fetch_result(items=[f.collected_item(source_id="hackernews")])
            )

    def test_item_ids_are_unique(self) -> None:
        with pytest.raises(ValidationError):
            SourceFetchResult.model_validate(
                f.source_fetch_result(items=[f.collected_item(), f.collected_item()])
            )

    @pytest.mark.parametrize("duration_ms", [-1, 1.5, "1200", True])
    def test_duration_is_a_non_negative_integer(self, duration_ms: object) -> None:
        with pytest.raises(ValidationError):
            SourceFetchResult.model_validate(f.source_fetch_result(duration_ms=duration_ms))

    def test_to_metrics_keeps_counts_without_items(self) -> None:
        result = SourceFetchResult.model_validate(f.source_fetch_result())
        metrics = result.to_metrics()
        assert metrics.item_count == 1
        assert "items" not in metrics.model_dump()


class TestNormalizedArticle:
    def test_valid_article(self) -> None:
        article = NormalizedArticle.model_validate(f.normalized_article())
        assert article.char_count == len(f.BODY)

    def test_content_hash_is_sha256_of_utf8_body(self) -> None:
        assert compute_content_hash(f.BODY) == f.BODY_HASH
        assert compute_content_hash("日本語") == compute_content_hash("日本語")
        assert compute_content_hash("a") != compute_content_hash("a ")

    def test_hash_must_match_body(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedArticle.model_validate(f.normalized_article(content_hash="0" * 64))

    @pytest.mark.parametrize("content_hash", ["ABC", "g" * 64, f.BODY_HASH.upper()])
    def test_hash_format(self, content_hash: str) -> None:
        with pytest.raises(ValidationError):
            NormalizedArticle.model_validate(f.normalized_article(content_hash=content_hash))

    @pytest.mark.parametrize(
        "field",
        ["canonical_url", "title", "published_at", "body_source", "body_text", "content_hash"],
    )
    def test_required_fields(self, field: str) -> None:
        data = f.normalized_article()
        del data[field]
        with pytest.raises(ValidationError):
            NormalizedArticle.model_validate(data)

    @pytest.mark.parametrize(
        "url",
        [
            "example.com/posts/1",
            "ftp://example.com/file",
            "https://",
            "https://example.com/has space",
            "javascript:alert(1)",
            "https://example.com/a\n",
            "https://user:secret@example.com/posts/1",
            "https://token@example.com/posts/1",
        ],
    )
    def test_canonical_url_must_be_absolute_http(self, url: str) -> None:
        with pytest.raises(ValidationError):
            NormalizedArticle.model_validate(f.normalized_article(canonical_url=url))

    @pytest.mark.parametrize("published_at", ["2026-09-17T06:00:00", "2026-09-17", "not a date"])
    def test_published_at_must_be_timezone_aware(self, published_at: str) -> None:
        with pytest.raises(ValidationError):
            NormalizedArticle.model_validate(f.normalized_article(published_at=published_at))

    def test_canonical_url_is_not_rewritten(self) -> None:
        article = NormalizedArticle.model_validate(
            f.normalized_article(canonical_url="https://example.com")
        )
        assert article.canonical_url == "https://example.com"

    @pytest.mark.parametrize("field", ["title", "body_text", "author"])
    def test_blank_text_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            NormalizedArticle.model_validate(f.normalized_article(**{field: "  \n"}))

    def test_author_is_optional(self) -> None:
        article = NormalizedArticle.model_validate(f.normalized_article(author=None))
        assert article.author is None

    def test_feed_fallback_is_a_body_source(self) -> None:
        article = NormalizedArticle.model_validate(f.normalized_article(body_source="feed_fallback"))
        assert article.body_source.value == "feed_fallback"

    def test_is_immutable(self) -> None:
        article = NormalizedArticle.model_validate(f.normalized_article())
        with pytest.raises(ValidationError):
            article.body_text = "changed"  # type: ignore[misc]


class TestProcessedState:
    def test_record_holds_only_the_four_allowed_fields(self) -> None:
        record = ProcessedRecord.model_validate(f.processed_record())
        assert set(record.model_dump()) == {
            "canonical_url",
            "first_seen_at",
            "content_hash",
            "last_decision",
        }

    @pytest.mark.parametrize("extra", ["body_text", "title", "decision_reason", "model_output"])
    def test_record_rejects_body_and_model_data(self, extra: str) -> None:
        with pytest.raises(ValidationError):
            ProcessedRecord.model_validate(f.processed_record(**{extra: "leak"}))

    def test_last_decision_domain(self) -> None:
        with pytest.raises(ValidationError):
            ProcessedRecord.model_validate(f.processed_record(last_decision="must_read"))

    def test_state_rejects_duplicate_url_and_hash_pairs(self) -> None:
        with pytest.raises(ValidationError):
            ProcessedState.model_validate(
                {"records": [f.processed_record(), f.processed_record(last_decision="excluded")]}
            )

    def test_state_allows_same_url_with_new_hash(self) -> None:
        state = ProcessedState.model_validate(
            {"records": [f.processed_record(), f.processed_record(content_hash="1" * 64)]}
        )
        assert state.schema_version == 1
        assert len(state.records) == 2

    def test_state_json_round_trip(self) -> None:
        state = ProcessedState.model_validate({"records": [f.processed_record()]})
        assert ProcessedState.model_validate_json(state.model_dump_json()) == state
