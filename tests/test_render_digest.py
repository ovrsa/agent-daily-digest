"""The digest body: snapshot, determinism, structure, caps and zero adoption."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import render_factories as f
from digest_contracts import (
    MUST_READ_MAX,
    WORTH_KNOWING_MAX,
    IncludedArticle,
    SelectorOutput,
    Tier,
)
from digest_render import MissingArticleError, TierCapError, digest_filename


def test_matches_the_snapshot_byte_for_byte() -> None:
    rendered = f.render()
    assert rendered is not None
    assert rendered.encode("utf-8") == f.DIGEST_SNAPSHOT_PATH.read_bytes()


def test_the_same_input_renders_the_same_bytes() -> None:
    first = f.render()
    second = f.render()
    assert first is not None and second is not None
    assert first.encode("utf-8") == second.encode("utf-8")


def test_the_iteration_order_of_the_article_mapping_does_not_change_the_output() -> None:
    """`articles` is a lookup table; only `SelectorOutput` decides the running order."""
    forward = f.articles()
    reversed_mapping = dict(reversed(list(forward.items())))
    assert list(reversed_mapping) != list(forward)
    assert f.render(articles=reversed_mapping) == f.render(articles=forward)


def test_the_running_order_is_the_order_selector_returned() -> None:
    payload = f.selector_payload()
    payload["must_read"] = list(reversed(payload["must_read"]))
    rendered = f.render(SelectorOutput.model_validate(payload))
    assert rendered is not None
    headings = [line for line in rendered.splitlines() if line.startswith("### ")]
    assert headings[0].startswith("### [Measuring retry cost")
    assert headings[2].startswith("### [Splitting plan and act")


def test_a_run_that_adopted_nothing_renders_nothing() -> None:
    assert f.render(f.empty_selector_output()) is None


def test_a_tier_with_no_entries_gets_no_heading() -> None:
    payload = f.selector_payload()
    payload["worth_knowing"] = []
    rendered = f.render(SelectorOutput.model_validate(payload))
    assert rendered is not None
    assert "## Must Read" in rendered
    assert "## Worth Knowing" not in rendered


@pytest.mark.parametrize(
    ("tier", "cap"), [(Tier.MUST_READ, MUST_READ_MAX), (Tier.WORTH_KNOWING, WORTH_KNOWING_MAX)]
)
def test_the_contract_rejects_more_entries_than_the_cap(tier: Tier, cap: int) -> None:
    payload = f.selector_payload()
    payload[tier.value] = [f.included_payload(f"cap{i:03d}") for i in range(cap + 1)]
    payload["must_read" if tier is Tier.WORTH_KNOWING else "worth_knowing"] = []
    with pytest.raises(ValidationError):
        SelectorOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("tier", "cap"), [(Tier.MUST_READ, MUST_READ_MAX), (Tier.WORTH_KNOWING, WORTH_KNOWING_MAX)]
)
def test_the_renderer_refuses_a_tier_over_the_cap(tier: Tier, cap: int) -> None:
    """`model_construct` skips validation, so the cap is re-checked before publishing."""
    buckets: dict[str, tuple[IncludedArticle, ...]] = {"must_read": (), "worth_knowing": ()}
    buckets[tier.value] = tuple(f.selector_output().must_read[0] for _ in range(cap + 1))
    over_cap = SelectorOutput.model_construct(
        excluded=(), duplicate_groups=(), **buckets
    )
    with pytest.raises(TierCapError) as raised:
        f.render(over_cap)
    assert raised.value.tier is tier
    assert (raised.value.count, raised.value.cap) == (cap + 1, cap)


def test_an_included_article_without_metadata_is_reported_not_guessed() -> None:
    articles = f.articles()
    del articles["a002"]
    del articles["a004"]
    with pytest.raises(MissingArticleError) as raised:
        f.render(articles=articles)
    assert raised.value.article_ids == ("a002", "a004")


def test_metadata_for_an_excluded_article_is_not_required() -> None:
    articles = {k: v for k, v in f.articles().items() if k != "a006"}
    assert f.render(articles=articles) is not None


def test_an_entry_without_a_caveat_omits_the_caveat_line() -> None:
    rendered = f.render()
    assert rendered is not None
    a002 = _section(rendered, "Per-tool permission scopes")
    assert "- 留保:" not in a002
    assert a002.count("- ") == 3


def test_a_caveat_without_evidence_ids_is_rendered_like_any_other() -> None:
    """留保 may have no Evidence ID (PR #14); that must not drop it from the digest."""
    selector_output = f.selector_output()
    caveat = selector_output.must_read[2].entry.caveat
    assert caveat is not None and caveat.evidence_ids == ()
    rendered = f.render()
    assert rendered is not None
    assert "- 留保: モデル価格は記事公開時点のもので、更新の有無は本文にない。" in rendered


def test_an_author_is_shown_only_when_the_article_has_one() -> None:
    rendered = f.render()
    assert rendered is not None
    assert "ソース: anthropic_engineering / 著者: Alice Kim / 公開: 2026-09-17" in rendered
    assert "ソース: github_releases / 公開: 2026-09-16" in rendered


def test_markdown_syntax_in_a_title_is_escaped_and_the_url_is_kept() -> None:
    rendered = f.render()
    assert rendered is not None
    assert "### [Per-tool permission scopes \\[v0.9\\]]" in rendered
    assert "(<https://example.com/releases/(2026-09-16)>)" in rendered


def test_a_newline_inside_a_field_stays_on_one_line() -> None:
    """`NonBlankStr` allows newlines; a raw one would change the list structure."""
    payload = f.selector_payload()
    payload["must_read"] = [
        f.included_payload(why_read="一行目\n  二行目\t三行目"),
        *payload["must_read"][1:],
    ]
    rendered = f.render(SelectorOutput.model_validate(payload))
    assert rendered is not None
    assert "- 読む理由: 一行目 二行目 三行目" in rendered


def test_the_published_date_keeps_the_offset_the_article_carries() -> None:
    late = f.articles()
    late["a001"] = f.normalized_article(
        published_at=datetime(2026, 9, 17, 23, 30, tzinfo=timezone(timedelta(hours=9)))
    )
    rendered = f.render(articles=late)
    assert rendered is not None
    assert "/ 公開: 2026-09-17" in rendered


def test_the_front_matter_carries_the_date_it_was_given() -> None:
    rendered = f.render(digest_date=date(2027, 1, 5))
    assert rendered is not None
    assert rendered.startswith("---\ndate: 2027-01-05\ntype: agent-daily-digest\n---\n")
    assert "generated_at" not in rendered


def test_the_document_ends_with_exactly_one_newline() -> None:
    rendered = f.render()
    assert rendered is not None
    assert rendered.endswith("。\n")
    assert not rendered.endswith("\n\n")


def test_the_filename_is_the_digest_date() -> None:
    assert digest_filename(date(2026, 9, 18)) == "2026-09-18.md"


def _section(rendered: str, title_fragment: str) -> str:
    sections = rendered.split("\n### ")
    return next(s for s in sections if title_fragment in s)
