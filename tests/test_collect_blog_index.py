"""Official blogs without feeds or sitemap dates use bounded head probes."""

from datetime import datetime, timezone

import collect_support as s

from agent_daily_digest.collect.run import collect_all

URL = "https://nousresearch.com/blog"
POST = "https://nousresearch.com/refactoring-hermes-with-1393-agents"


def test_blog_index_uses_original_publication_date() -> None:
    config = s.config(
        {
            "id": "nous_blog",
            "kind": "fixed_watch",
            "connector": "blog_index",
            "url": URL,
            "url_prefix": "https://nousresearch.com/",
            "link_class": "nw-catalogue-link",
            "max_metadata_probes": 5,
        }
    )
    fetcher = s.FixtureFetcher({URL: s.read("nous_blog.html"), POST: s.read("nous_post_head.html")})
    report = collect_all(config, fetcher=fetcher, now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    assert report.failed_source_ids == ()
    assert [(i.url, i.title, i.published_at) for i in report.items] == [
        (POST, "Refactoring Hermes with 1,393 agents", "2026-09-15T15:00:00+00:00")
    ]
    assert report.probe("nous_blog").attempted == 1
    assert collect_all(config, fetcher=fetcher, now=datetime(2026, 9, 29, tzinfo=timezone.utc)).items == ()


def test_probes_only_allowed_unique_unknown_links_up_to_cap() -> None:
    body = b"""<a class="nw-catalogue-link" href="https://evil.example/post">offsite</a>
    <a class="nw-catalogue-link" href="https://nousresearch.com/known">known</a>
    <a class="nw-catalogue-link" href="/refactoring-hermes-with-1393-agents">first</a>
    <a class="nw-catalogue-link" href="/refactoring-hermes-with-1393-agents">duplicate</a>
    <a class="nw-catalogue-link" href="/later">later</a>"""
    config = s.config(
        {
            "id": "nous_blog",
            "kind": "fixed_watch",
            "connector": "blog_index",
            "url": URL,
            "url_prefix": "https://nousresearch.com/",
            "link_class": "nw-catalogue-link",
            "max_metadata_probes": 1,
        }
    )
    fetcher = s.FixtureFetcher({URL: body, POST: s.read("nous_post_head.html")})
    report = collect_all(
        config,
        fetcher=fetcher,
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        known_urls=frozenset({"https://nousresearch.com/known"}),
    )
    assert report.failed_source_ids == ()
    assert [i.url for i in report.items] == [POST]
    assert fetcher.requested == [URL, POST]
    assert report.probe("nous_blog").skipped_over_cap == 1


def test_missing_publication_metadata_is_reported_as_failure() -> None:
    config = s.config(
        {
            "id": "nous_blog",
            "kind": "fixed_watch",
            "connector": "blog_index",
            "url": URL,
            "url_prefix": "https://nousresearch.com/",
            "link_class": "nw-catalogue-link",
            "max_metadata_probes": 5,
        }
    )
    fetcher = s.FixtureFetcher(
        {URL: s.read("nous_blog.html"), POST: b"<html><head><title>Blocked</title></head></html>"}
    )
    report = collect_all(config, fetcher=fetcher, now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    assert report.failed_source_ids == ("nous_blog",)
