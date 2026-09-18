"""Each connector against the response its source actually served."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from digest_collect import (
    CollectContext,
    PageMetadata,
    ProbeRecorder,
    make_article_id,
    read_head_metadata,
)
from digest_collect.config import SOURCE_SPEC_ADAPTER
from digest_collect.connectors import CONNECTORS, parse_w3c_datetime

import collect_support as s

CLAUDE_POST = "https://claude.com/blog/build-artifacts"
ANTHROPIC_POST = "https://www.anthropic.com/engineering/building-effective-agents"


def spec(data: dict):
    return SOURCE_SPEC_ADAPTER.validate_python(data)


def context(fetcher, *, max_items: int = 12, window_days: int = 7, known_urls=frozenset()):
    return CollectContext(
        fetcher=fetcher,
        now=s.NOW,
        window=dt.timedelta(days=window_days),
        max_items=max_items,
        probe=ProbeRecorder(),
        known_urls=known_urls,
    )


def run(source: dict, fetcher, **kwargs):
    parsed = spec(source)
    ctx = context(fetcher, **kwargs)
    return CONNECTORS[parsed.connector](parsed, ctx), ctx


# --------------------------------------------------------------------------


class TestFeedConnector:
    RECORDED = {
        "simonw": ("simonwillison_atom.xml", "https://simonwillison.net/atom/everything/"),
        "boristane": ("boristane_rss.xml", "https://boristane.com/rss.xml"),
        "nyosegawa": ("nyosegawa_rss.xml", "https://nyosegawa.com/feed.xml"),
        "addyosmani": ("addyosmani_rss.xml", "https://addyosmani.com/rss.xml"),
        "latent_space": ("latent_space_rss.xml", "https://www.latent.space/feed"),
        "interconnects": ("interconnects_rss.xml", "https://www.interconnects.ai/feed"),
        "ai_news_smol": ("ai_news_smol_rss.xml", "https://buttondown.com/ainews/rss"),
        "reddit_localllama": (
            "reddit_localllama_atom.xml",
            "https://www.reddit.com/r/LocalLLaMA/top.rss?t=day&limit=10",
        ),
    }

    @pytest.mark.parametrize(("source_id", "recorded"), sorted(RECORDED.items()))
    def test_every_recorded_feed_yields_identified_candidates(
        self, source_id: str, recorded: tuple[str, str]
    ) -> None:
        name, url = recorded
        items, _ = run(
            s.feed_source(source_id, url=url), s.FixtureFetcher({url: s.read(name)})
        )
        assert items
        for item in items:
            assert item.source_id == source_id
            assert item.title
            assert item.url and item.url.startswith("http")
            assert item.published_at

    def test_the_source_kind_from_the_registry_reaches_every_item(self) -> None:
        url = "https://www.reddit.com/r/LocalLLaMA/top.rss?t=day&limit=10"
        items, _ = run(
            s.feed_source("reddit_localllama", url=url) | {"kind": "discovery"},
            s.FixtureFetcher({url: s.read("reddit_localllama_atom.xml")}),
        )
        assert {item.source_kind.value for item in items} == {"discovery"}

    def test_an_atom_entry_uses_the_alternate_link_not_the_id(self) -> None:
        url = "https://simonwillison.net/atom/everything/"
        items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: s.read("simonwillison_atom.xml")}))
        assert items[0].url.startswith("https://simonwillison.net/2026/")

    def test_a_feed_with_no_entries_succeeds_with_nothing(self) -> None:
        url = "https://example.com/feed"
        empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
        items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: empty}))
        assert items == ()

    def test_an_empty_atom_feed_succeeds_with_nothing(self) -> None:
        url = "https://example.com/feed"
        empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
        items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: empty}))
        assert items == ()

    def test_a_broken_feed_raises_so_the_collector_can_record_the_reason(self) -> None:
        url = "https://example.com/feed"
        with pytest.raises(Exception):
            run(s.feed_source(url=url), s.FixtureFetcher({url: b"<rss><channel"}))

    def test_the_item_cap_is_respected(self) -> None:
        url = "https://boristane.com/rss.xml"
        items, _ = run(
            s.feed_source(url=url), s.FixtureFetcher({url: s.read("boristane_rss.xml")}), max_items=1
        )
        assert len(items) == 1

    def test_already_processed_urls_are_left_out(self) -> None:
        url = "https://boristane.com/rss.xml"
        all_items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: s.read("boristane_rss.xml")}))
        skipped = all_items[0].url
        kept, _ = run(
            s.feed_source(url=url),
            s.FixtureFetcher({url: s.read("boristane_rss.xml")}),
            known_urls=frozenset({skipped}),
        )
        assert skipped not in {item.url for item in kept}
        assert len(kept) == len(all_items) - 1

    def test_feed_summaries_are_stripped_of_markup(self) -> None:
        url = "https://example.com/feed"
        feed = (
            b'<?xml version="1.0"?><rss version="2.0"><channel><item>'
            b"<title>T</title><link>https://example.com/a</link>"
            b"<pubDate>Fri, 21 Aug 2026 00:00:00 GMT</pubDate>"
            b"<description>&lt;p&gt;one&lt;/p&gt; &lt;b&gt;two&lt;/b&gt;</description>"
            b"</item></channel></rss>"
        )
        items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: feed}))
        assert items[0].feed_summary == "one two"

    def test_a_feed_item_without_a_date_still_becomes_a_candidate(self) -> None:
        url = "https://example.com/feed"
        feed = (
            b'<?xml version="1.0"?><rss version="2.0"><channel><item>'
            b"<title>T</title><link>https://example.com/a</link></item></channel></rss>"
        )
        items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: feed}))
        # The `required_fields` gate records the reason; the collector does not drop it.
        assert items[0].published_at is None

    def test_published_at_is_handed_over_as_the_raw_text(self) -> None:
        url = "https://boristane.com/rss.xml"
        items, _ = run(s.feed_source(url=url), s.FixtureFetcher({url: s.read("boristane_rss.xml")}))
        assert items[0].published_at == "Fri, 21 Aug 2026 00:00:00 GMT"


class TestArticleId:
    def test_an_id_is_stable_across_runs(self) -> None:
        assert make_article_id("simonw", "https://a.example/x") == make_article_id(
            "simonw", "https://a.example/x"
        )

    def test_the_same_url_under_two_sources_gets_two_ids(self) -> None:
        assert make_article_id("simonw", "https://a.example/x") != make_article_id(
            "boristane", "https://a.example/x"
        )

    def test_an_id_fits_the_contract_pattern(self) -> None:
        import re

        value = make_article_id("simonw", "https://a.example/x")
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value)


# --------------------------------------------------------------------------


class TestReadHeadMetadata:
    def test_a_json_ld_blog_posting_supplies_the_title_and_date(self) -> None:
        meta = read_head_metadata(s.read("claude_post_head.html"))
        assert meta.title == "Turn ideas into interactive AI-powered apps"
        assert meta.published_at == "Jun 25, 2025"

    def test_a_page_without_json_ld_falls_back_to_og_title(self) -> None:
        meta = read_head_metadata(s.read("anthropic_post_head.html"))
        assert meta.title == "Building Effective AI Agents"
        # No publication date in the head; the sitemap's lastmod is all there is.
        assert meta.published_at is None

    def test_attribute_order_does_not_matter(self) -> None:
        page = b'<html><head><meta content="T" property="og:title"/></head><body></body></html>'
        assert read_head_metadata(page).title == "T"

    def test_the_title_element_is_the_last_resort(self) -> None:
        page = b"<html><head><title>Only a title</title></head><body></body></html>"
        assert read_head_metadata(page).title == "Only a title"

    def test_html_entities_are_unescaped(self) -> None:
        page = b'<html><head><meta property="og:title" content="A &amp; B"/></head></html>'
        assert read_head_metadata(page).title == "A & B"

    def test_nothing_in_the_head_yields_nothing(self) -> None:
        assert read_head_metadata(b"<html><head></head><body>text</body></html>") == PageMetadata(
            None, None, None
        )

    def test_the_body_is_not_read(self) -> None:
        page = b"<html><head></head><body><h1>Body heading</h1><title>x</title></body></html>"
        assert read_head_metadata(page).title is None

    def test_broken_json_ld_does_not_break_the_probe(self) -> None:
        page = (
            b'<html><head><script type="application/ld+json">{not json</script>'
            b'<meta property="og:title" content="T"/></head></html>'
        )
        assert read_head_metadata(page).title == "T"

    def test_a_json_ld_graph_is_searched(self) -> None:
        payload = json.dumps(
            {"@graph": [{"@type": "WebSite"}, {"@type": "Article", "headline": "H"}]}
        ).encode()
        page = b'<html><head><script type="application/ld+json">' + payload + b"</script></head></html>"
        assert read_head_metadata(page).title == "H"

    def test_a_non_article_json_ld_node_is_ignored(self) -> None:
        payload = json.dumps({"@type": "WebPage", "headline": "Not an article"}).encode()
        page = (
            b'<html><head><script type="application/ld+json">' + payload + b"</script>"
            b"<title>Real</title></head></html>"
        )
        assert read_head_metadata(page).title == "Real"

    def test_undecodable_bytes_do_not_raise(self) -> None:
        page = b'<html><head><meta property="og:title" content="\xff\xfe"/></head></html>'
        assert read_head_metadata(page).title is not None


class TestParseW3CDatetime:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-09-17T13:09:38.431Z", dt.datetime(2026, 9, 17, 13, 9, 38, 431000, tzinfo=dt.timezone.utc)),
            ("2026-09-17T13:09:38Z", dt.datetime(2026, 9, 17, 13, 9, 38, tzinfo=dt.timezone.utc)),
            ("2026-09-17", dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc)),
            ("2026-09-17T13:09:38+09:00", dt.datetime(2026, 9, 17, 13, 9, 38, tzinfo=dt.timezone(dt.timedelta(hours=9)))),
        ],
    )
    def test_accepted_forms(self, value: str, expected: dt.datetime) -> None:
        assert parse_w3c_datetime(value) == expected

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        assert parse_w3c_datetime("2026-09-17T13:09:38").tzinfo == dt.timezone.utc

    @pytest.mark.parametrize("value", [None, "", "   ", "Jun 02, 2026", "not a date"])
    def test_unparseable_values_yield_nothing(self, value: str | None) -> None:
        assert parse_w3c_datetime(value) is None


class TestSitemapConnector:
    def routes(self, **extra):
        return {
            s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml"),
            CLAUDE_POST: s.read("claude_post_head.html"),
            **extra,
        }

    def test_only_urls_under_the_prefix_become_candidates(self) -> None:
        items, _ = run(s.sitemap_source(), s.FixtureFetcher(self.routes(), default=b"<html></html>"))
        assert items
        assert all(item.url.startswith("https://claude.com/blog/") for item in items)

    def test_a_probed_page_supplies_the_title_and_its_own_date(self) -> None:
        items, ctx = run(s.sitemap_source(), s.FixtureFetcher(self.routes(), default=b"<html></html>"))
        probed = next(item for item in items if item.url == CLAUDE_POST)
        assert probed.title == "Turn ideas into interactive AI-powered apps"
        assert probed.published_at == "Jun 25, 2025"
        assert ctx.probe.succeeded >= 1

    def test_a_page_without_a_date_keeps_the_sitemap_lastmod(self) -> None:
        routes = {
            s.ANTHROPIC_SITEMAP: s.read("anthropic_sitemap.xml"),
            ANTHROPIC_POST: s.read("anthropic_post_head.html"),
        }
        items, _ = run(
            s.sitemap_source(
                "anthropic_engineering",
                url=s.ANTHROPIC_SITEMAP,
                url_prefix="https://www.anthropic.com/engineering/",
            ),
            s.FixtureFetcher(routes, default=b"<html></html>"),
            window_days=90,
        )
        probed = next(item for item in items if item.url == ANTHROPIC_POST)
        assert probed.title == "Building Effective AI Agents"
        assert probed.published_at == "2026-08-10T22:57:30.000Z"

    def test_candidates_outside_the_window_are_left_out(self) -> None:
        fetcher = s.FixtureFetcher(self.routes(), default=b"<html></html>")
        inside, _ = run(s.sitemap_source(), fetcher, window_days=7)
        outside, _ = run(
            s.sitemap_source(), s.FixtureFetcher(self.routes(), default=b"<html></html>"), window_days=1
        )
        assert len(outside) < len(inside)

    def test_nothing_in_the_window_succeeds_with_nothing_and_probes_nothing(self) -> None:
        fetcher = s.FixtureFetcher({s.ANTHROPIC_SITEMAP: s.read("anthropic_sitemap.xml")})
        items, ctx = run(
            s.sitemap_source(
                "anthropic_engineering",
                url=s.ANTHROPIC_SITEMAP,
                url_prefix="https://www.anthropic.com/engineering/",
            ),
            fetcher,
            window_days=7,
        )
        assert items == ()
        assert ctx.probe.attempted == 0
        assert fetcher.requested == [s.ANTHROPIC_SITEMAP]

    def test_the_probe_cap_bounds_the_extra_requests(self) -> None:
        fetcher = s.FixtureFetcher(self.routes(), default=b"<html></html>")
        items, ctx = run(s.sitemap_source(max_metadata_probes=1), fetcher)
        assert len(items) > 1
        assert ctx.probe.attempted == 1
        assert ctx.probe.skipped_over_cap == len(items) - 1
        assert len(fetcher.requested) == 2  # the sitemap plus one page

    def test_a_cap_of_zero_probes_nothing_but_still_reports_candidates(self) -> None:
        fetcher = s.FixtureFetcher(self.routes(), default=b"<html></html>")
        items, ctx = run(s.sitemap_source(max_metadata_probes=0), fetcher)
        assert items
        assert ctx.probe.attempted == 0
        assert all(item.title is None for item in items)

    def test_the_most_recently_modified_candidates_are_probed_first(self) -> None:
        fetcher = s.FixtureFetcher(self.routes(), default=b"<html></html>")
        items, _ = run(s.sitemap_source(max_metadata_probes=2), fetcher)
        assert fetcher.requested[1:3] == [items[0].url, items[1].url]

    def test_a_failed_probe_leaves_the_item_without_a_guessed_title(self) -> None:
        fetcher = s.FixtureFetcher(
            {s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml")}, default=s.http_error(404)
        )
        items, ctx = run(s.sitemap_source(max_metadata_probes=2), fetcher)
        assert items
        assert all(item.title is None for item in items)
        assert ctx.probe.attempted == 2
        assert ctx.probe.failed == 2
        assert ctx.probe.succeeded == 0

    def test_a_timed_out_probe_does_not_fail_the_source(self) -> None:
        fetcher = s.FixtureFetcher(
            {s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml")}, default=s.timeout()
        )
        items, ctx = run(s.sitemap_source(max_metadata_probes=1), fetcher)
        assert items
        assert ctx.probe.failed == 1

    def test_a_failed_probe_still_leaves_the_lastmod_as_the_date(self) -> None:
        fetcher = s.FixtureFetcher(
            {s.CLAUDE_SITEMAP: s.read("claude_sitemap.xml")}, default=s.http_error(500)
        )
        items, _ = run(s.sitemap_source(max_metadata_probes=1), fetcher)
        assert items[0].published_at.startswith("2026-09-")

    def test_probe_time_is_recorded(self) -> None:
        fetcher = s.FixtureFetcher(self.routes(), default=b"<html></html>")
        _, ctx = run(s.sitemap_source(), fetcher)
        assert ctx.probe.duration_ns > 0

    def test_a_broken_sitemap_raises(self) -> None:
        with pytest.raises(Exception):
            run(s.sitemap_source(), s.FixtureFetcher({s.CLAUDE_SITEMAP: b"<urlset"}))

    def test_already_processed_urls_are_never_probed(self) -> None:
        first, _ = run(s.sitemap_source(), s.FixtureFetcher(self.routes(), default=b"<html></html>"))
        known = frozenset({item.url for item in first})
        fetcher = s.FixtureFetcher(self.routes(), default=b"<html></html>")
        items, ctx = run(s.sitemap_source(), fetcher, known_urls=known)
        assert items == ()
        assert ctx.probe.attempted == 0

    def test_a_url_without_a_lastmod_is_left_out(self) -> None:
        sitemap = (
            b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://claude.com/blog/a</loc></url></urlset>"
        )
        items, _ = run(s.sitemap_source(), s.FixtureFetcher({s.CLAUDE_SITEMAP: sitemap}))
        assert items == ()


class TestHackerNewsConnector:
    SPEC = {
        "id": "hackernews",
        "kind": "discovery",
        "connector": "hackernews",
        "enabled": True,
        "url": "https://hn.algolia.com/api/v1/search",
        "keywords": ["coding agent"],
        "min_points": 30,
    }

    def test_a_recorded_search_becomes_candidates(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=s.read("hn_algolia.json")))
        assert len(items) == 4
        assert items[0].title.startswith("HarnessTax")

    def test_items_are_ordered_by_points(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=s.read("hn_algolia.json")))
        payload = json.loads(s.read("hn_algolia.json"))
        by_title = {hit["title"]: hit["points"] for hit in payload["hits"]}
        points = [by_title[item.title] for item in items]
        assert points == sorted(points, reverse=True)
        assert len(set(points)) == len(points)  # the fixture has no ties to hide a stable sort

    def test_a_story_without_a_url_falls_back_to_the_discussion(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=s.read("hn_algolia.json")))
        assert any(
            item.url == "https://news.ycombinator.com/item?id=49743049" for item in items
        )

    def test_the_same_story_from_two_keywords_is_kept_once(self) -> None:
        spec_two = self.SPEC | {"keywords": ["coding agent", "llm"]}
        items, _ = run(spec_two, s.FixtureFetcher({}, default=s.read("hn_algolia.json")))
        assert len({item.article_id for item in items}) == len(items) == 4

    def test_one_failing_keyword_does_not_lose_the_others(self) -> None:
        good = s.read("hn_algolia.json")

        class Flaky:
            requested: list[str] = []

            def get(self, url, *, accept="*/*"):
                self.requested.append(url)
                if "llm" in url:
                    raise s.http_error(503)
                from digest_collect import HttpResponse

                return HttpResponse(url=url, status=200, body=good)

        items, _ = run(self.SPEC | {"keywords": ["llm", "coding agent"]}, Flaky())
        assert len(items) == 4

    def test_every_keyword_failing_fails_the_source(self) -> None:
        with pytest.raises(Exception):
            run(self.SPEC, s.FixtureFetcher({}, default=s.http_error(503)))

    def test_an_empty_result_set_succeeds_with_nothing(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=b'{"hits": []}'))
        assert items == ()

    def test_the_query_carries_the_window_and_the_point_floor(self) -> None:
        fetcher = s.FixtureFetcher({}, default=b'{"hits": []}')
        run(self.SPEC | {"window_hours": 48}, fetcher)
        asked = fetcher.requested[0]
        assert "points%3E30" in asked
        since = int((s.NOW - dt.timedelta(hours=48)).timestamp())
        assert str(since) in asked

    def test_the_item_cap_is_respected(self) -> None:
        items, _ = run(
            self.SPEC, s.FixtureFetcher({}, default=s.read("hn_algolia.json")), max_items=2
        )
        assert len(items) == 2


class TestHuggingFacePapersConnector:
    SPEC = {
        "id": "hf_papers",
        "kind": "fixed_watch",
        "connector": "hf_papers",
        "enabled": True,
        "url": "https://huggingface.co/api/daily_papers",
    }

    def test_a_recorded_payload_becomes_candidates(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=s.read("hf_papers.json")))
        assert len(items) == 3
        assert all(item.url.startswith("https://huggingface.co/papers/") for item in items)
        assert all(item.title for item in items)

    def test_todays_rejected_request_falls_back_to_yesterday(self) -> None:
        today = f"{self.SPEC['url']}?date=2026-09-18"
        yesterday = f"{self.SPEC['url']}?date=2026-09-17"
        # Observed on 2026-09-18: the API answers 400 until the day is published.
        fetcher = s.FixtureFetcher(
            {today: s.http_error(400), yesterday: s.read("hf_papers.json")}
        )
        items, _ = run(self.SPEC, fetcher)
        assert len(items) == 3
        assert fetcher.requested == [today, yesterday]

    def test_an_empty_day_falls_back_to_the_day_before(self) -> None:
        today = f"{self.SPEC['url']}?date=2026-09-18"
        yesterday = f"{self.SPEC['url']}?date=2026-09-17"
        fetcher = s.FixtureFetcher({today: b"[]", yesterday: s.read("hf_papers.json")})
        items, _ = run(self.SPEC, fetcher)
        assert len(items) == 3

    def test_both_days_failing_fails_the_source(self) -> None:
        with pytest.raises(Exception):
            run(self.SPEC, s.FixtureFetcher({}, default=s.http_error(500)))

    def test_a_payload_that_is_not_a_list_yields_nothing(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=b'{"error": "x"}'))
        assert items == ()

    def test_an_entry_without_a_paper_id_is_skipped(self) -> None:
        payload = json.dumps([{"title": "T", "paper": {}}]).encode()
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=payload))
        assert items == ()


class TestGitHubReleasesConnector:
    SPEC = {
        "id": "gh_anthropics_claude-code",
        "kind": "fixed_watch",
        "connector": "gh_releases",
        "enabled": True,
        "repo": "anthropics/claude-code",
    }

    def test_recent_releases_become_candidates(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=s.read("gh_releases.json")))
        assert items
        assert all(item.title.startswith("anthropics/claude-code ") for item in items)

    def test_releases_older_than_the_window_are_left_out(self) -> None:
        items, _ = run(
            self.SPEC, s.FixtureFetcher({}, default=s.read("gh_releases.json")), window_days=1
        )
        older, _ = run(
            self.SPEC, s.FixtureFetcher({}, default=s.read("gh_releases.json")), window_days=7
        )
        assert len(items) < len(older)

    def test_a_repository_with_no_releases_succeeds_with_nothing(self) -> None:
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=b"[]"))
        assert items == ()

    def test_a_missing_release_page_falls_back_to_the_releases_index(self) -> None:
        payload = json.dumps(
            [{"tag_name": "v1", "published_at": "2026-09-17T00:00:00Z"}]
        ).encode()
        items, _ = run(self.SPEC, s.FixtureFetcher({}, default=payload))
        assert items[0].url == "https://github.com/anthropics/claude-code/releases"

    def test_the_request_carries_the_repository(self) -> None:
        fetcher = s.FixtureFetcher({}, default=b"[]")
        run(self.SPEC, fetcher)
        assert fetcher.requested == [
            "https://api.github.com/repos/anthropics/claude-code/releases?per_page=5"
        ]
