"""Collect original dated posts from the official Hermes story directory."""

from datetime import datetime, timezone

import collect_support as s

from agent_daily_digest.collect.run import collect_all

URL = "https://hermes-agent.nousresearch.com/docs/user-stories/"


def test_collect_original_posts_with_dates() -> None:
    config = s.config(
        {"id": "hermes_stories", "kind": "discovery", "connector": "hermes_stories", "url": URL}
    )
    report = collect_all(
        config,
        fetcher=s.FixtureFetcher({URL: s.read("hermes_stories.html")}),
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    assert report.failed_source_ids == ()
    assert [(i.url, i.published_at) for i in report.items] == [
        ("https://www.reddit.com/r/hermesagent/comments/1v06ap3/", "2026-07-18")
    ]


def test_skips_known_and_unsafe_urls_and_takes_newest_unique_posts() -> None:
    def card(url, date):
        return f'<a class="tile_hash" href="{url}"><h3 class="headline_hash">Example</h3><span class="author_hash">Author · {date}</span></a>'

    body = "".join(
        [
            card("https://example.com/known", "2026-07-18"),
            card("javascript:alert(1)", "2026-07-18"),
            card("https://example.com/old", "2026-07-13"),
            card("https://example.com/new", "2026-07-19"),
            card("https://example.com/new", "2026-07-19"),
            card("https://example.com/future", "2026-07-21"),
            card("https://example.com/no-date", "2026"),
        ]
    ).encode()
    config = s.config(
        {"id": "hermes_stories", "kind": "discovery", "connector": "hermes_stories", "url": URL}, max_items=2
    )
    report = collect_all(
        config,
        fetcher=s.FixtureFetcher({URL: body}),
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
        known_urls=frozenset({"https://example.com/known"}),
    )
    assert report.failed_source_ids == ()
    assert [i.url for i in report.items] == ["https://example.com/new", "https://example.com/old"]


def test_changed_directory_markup_is_a_failure_not_a_quiet_day() -> None:
    config = s.config(
        {"id": "hermes_stories", "kind": "discovery", "connector": "hermes_stories", "url": URL}
    )
    report = collect_all(
        config,
        fetcher=s.FixtureFetcher({URL: b"<html>Sign in</html>"}),
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    assert report.failed_source_ids == ("hermes_stories",)
    assert report.result("hermes_stories").failure.kind.value == "parse"


def test_missing_date_markup_does_not_silently_drop_all_stories() -> None:
    body = b'<a class="tile_hash" href="https://example.com/story"><h3 class="headline_hash">Story</h3><span class="changed_author">2026-07-18</span></a>'
    config = s.config(
        {"id": "hermes_stories", "kind": "discovery", "connector": "hermes_stories", "url": URL}
    )
    report = collect_all(
        config, fetcher=s.FixtureFetcher({URL: body}), now=datetime(2026, 7, 19, tzinfo=timezone.utc)
    )
    assert report.failed_source_ids == ("hermes_stories",)
