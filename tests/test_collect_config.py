"""The registry in `config.json`, and that it has not drifted from the legacy keys."""

from __future__ import annotations

import json
from pathlib import Path

import collect_support as s
import pytest
from pydantic import ValidationError

from agent_daily_digest.collect.run import (
    CollectionConfig,
    SitemapSource,
    load_collection_config,
)
from agent_daily_digest.collect.transport import UrllibFetcher, make_fetcher
from agent_daily_digest.contracts.articles import SourceKind

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config.json"

REQUIRED_BLOG_URLS = {
    "claude_blog": "https://claude.com/blog/",
    "boristane": "https://boristane.com/",
    "nyosegawa": "https://nyosegawa.com/",
    "anthropic_engineering": "https://www.anthropic.com/engineering/",
    "simonw": "https://simonwillison.net/",
    "addyosmani": "https://addyosmani.com/",
}
"""The six blogs #1 says to check every run, by the source that covers each."""

CLAUDE_BLOG_WINDOW_VOLUME = 34
"""URLs under `https://claude.com/blog/` with a `lastmod` inside the 7-day
window, counted from the live sitemap on 2026-09-18.

The site re-generates in bulk — 23 of the 34 moved on one day — so a cap
below this fills up with old posts whose `lastmod` happens to be recent and
pushes the day's new ones out."""

NOTABLE_BLOG_FEEDS = {
    "goedecke": "https://www.seangoedecke.com/rss.xml",
    "syu-m-5151": "https://syu-m-5151.hatenablog.com/feed",
    "mizchi": "https://zenn.dev/mizchi/feed",
    "ronacher": "https://lucumr.pocoo.org/feed.atom",
    "breunig": "https://www.dbreunig.com/feed.xml",
    "harper": "https://harper.blog/index.xml",
    "hamel": "https://hamel.dev/index.xml",
    "pragmatic_engineer": "https://newsletter.pragmaticengineer.com/feed",
    "karpathy": "https://karpathy.bearblog.dev/feed/",
    "mitchellh": "https://mitchellh.com/feed.xml",
    "ghuntley": "https://ghuntley.com/rss/",
    "yegge": "https://steve-yegge.medium.com/feed",
    "lilianweng": "https://lilianweng.github.io/index.xml",
    "eugeneyan": "https://eugeneyan.com/rss/",
    "litt": "https://www.geoffreylitt.com/feed.xml",
    "huyenchip": "https://huyenchip.com/feed.xml",
    "steipete": "https://steipete.me/rss.xml",
}
"""Blogs by well-known developers and researchers who write about using coding agents (#31).

The first eight posted about coding agents in the 60 days to 2026-09-25. The
rest post rarely; a quiet feed costs one request a run and no research."""


@pytest.fixture(scope="module")
def loaded() -> CollectionConfig:
    return load_collection_config(CONFIG_PATH)


class TestShippedRegistry:
    def test_the_repository_config_validates(self, loaded: CollectionConfig) -> None:
        assert loaded.sources

    def test_source_ids_are_unique(self, loaded: CollectionConfig) -> None:
        ids = [source.id for source in loaded.sources]
        assert len(set(ids)) == len(ids)

    @pytest.mark.parametrize(("source_id", "url"), sorted(REQUIRED_BLOG_URLS.items()))
    def test_every_required_blog_is_covered_by_an_enabled_fixed_watch_source(
        self, loaded: CollectionConfig, source_id: str, url: str
    ) -> None:
        spec = loaded.source(source_id)
        assert spec.enabled
        assert spec.kind is SourceKind.FIXED_WATCH
        reached = spec.url_prefix if isinstance(spec, SitemapSource) else spec.url
        assert url in reached or reached.startswith(url)

    def test_both_source_kinds_are_in_use(self, loaded: CollectionConfig) -> None:
        kinds = {source.kind for source in loaded.sources}
        assert kinds == {SourceKind.FIXED_WATCH, SourceKind.DISCOVERY}

    def test_discovery_sources_cover_coding_and_personal_agents(
        self, loaded: CollectionConfig
    ) -> None:
        discovery = {s.id for s in loaded.sources if s.kind is SourceKind.DISCOVERY}
        assert discovery == {"hackernews", "reddit_coding_agents", "arxiv_surveys", "hermes_stories", "reddit_personal_agents"}

    def test_no_release_notes_or_papers_are_collected(self, loaded: CollectionConfig) -> None:
        # #31: release notes were mostly bug fixes and the daily papers seldom
        # touched how coding agents are used; big releases reach the blogs.
        assert {spec.connector for spec in loaded.sources} <= {"feed", "sitemap", "hackernews", "hermes_stories", "blog_index"}

    def test_reddit_groups_coding_and_personal_agent_subreddits(self, loaded: CollectionConfig) -> None:
        # #38: one request for all three. Reddit answers 429 after a dozen or so
        # unauthenticated requests, and each item costs a page fetch as well.
        reddit = [spec for spec in loaded.sources if "reddit.com" in getattr(spec, "url", "")]
        assert [spec.id for spec in reddit] == ["reddit_coding_agents", "reddit_personal_agents"]
        spec = reddit[0]
        assert spec.connector == "feed"
        assert spec.url.startswith("https://www.reddit.com/r/ClaudeAI+ClaudeCode+codex/top.rss?")
        assert "t=day" in spec.url and "limit=5" in spec.url
        assert loaded.items_for(spec) == 5

    def test_openai_is_a_fixed_watch_feed_narrowed_to_four_categories(
        self, loaded: CollectionConfig
    ) -> None:
        # #38: most of the week's fifteen posts are policy, partnerships and
        # customer stories, which would take research slots from the other blogs.
        spec = loaded.source("openai_news")
        assert spec.enabled
        assert spec.kind is SourceKind.FIXED_WATCH
        assert spec.url == "https://openai.com/news/rss.xml"
        assert set(spec.categories) == {"Product", "Research", "Engineering", "Applied AI"}

    def test_cursor_is_its_changelog_feed(self, loaded: CollectionConfig) -> None:
        # #38: the blog has no feed, and its sitemap dates every page today.
        spec = loaded.source("cursor_changelog")
        assert spec.enabled
        assert spec.kind is SourceKind.FIXED_WATCH
        assert spec.url == "https://cursor.com/changelog/rss.xml"
        assert not any("cursor.com/blog" in getattr(other, "url_prefix", "") for other in loaded.sources)

    @pytest.mark.parametrize(("source_id", "url"), sorted(NOTABLE_BLOG_FEEDS.items()))
    def test_every_notable_blog_is_an_enabled_fixed_watch_feed(
        self, loaded: CollectionConfig, source_id: str, url: str
    ) -> None:
        spec = loaded.source(source_id)
        assert spec.enabled
        assert spec.kind is SourceKind.FIXED_WATCH
        assert spec.connector == "feed"
        assert spec.url == url

    def test_the_arxiv_source_asks_for_the_newest_software_engineering_surveys_on_agents(
        self, loaded: CollectionConfig
    ) -> None:
        spec = loaded.source("arxiv_surveys")
        assert spec.connector == "feed"
        assert spec.url.startswith("https://export.arxiv.org/api/query?")
        for term in ("cat:cs.SE", "ti:survey", "abs:agent", "sortBy=submittedDate", "sortOrder=descending"):
            assert term in spec.url

    def test_the_two_blogs_without_a_feed_use_the_sitemap_connector(
        self, loaded: CollectionConfig
    ) -> None:
        # Probed on 2026-09-18: /rss.xml, /feed.xml and /blog/rss.xml all 404
        # on both hosts, and robots.txt declares a sitemap on both.
        for source_id in ("claude_blog", "anthropic_engineering"):
            assert loaded.source(source_id).connector == "sitemap"

    def test_every_other_required_blog_uses_a_feed(self, loaded: CollectionConfig) -> None:
        for source_id in ("simonw", "boristane", "nyosegawa", "addyosmani"):
            assert loaded.source(source_id).connector == "feed"

    def test_the_claude_blog_caps_reach_the_volume_its_sitemap_shows(
        self, loaded: CollectionConfig
    ) -> None:
        # Both caps have to clear the volume or the other one truncates. A
        # cap on items alone lets the extra candidates through without a
        # title, and #5 drops those on `missing_title`.
        spec = loaded.source("claude_blog")
        assert loaded.items_for(spec) >= CLAUDE_BLOG_WINDOW_VOLUME
        assert spec.max_metadata_probes >= CLAUDE_BLOG_WINDOW_VOLUME

    def test_the_shipped_http_block_builds_the_fetcher_the_run_uses(
        self, loaded: CollectionConfig
    ) -> None:
        assert isinstance(make_fetcher(loaded.http), UrllibFetcher)

    def test_metadata_probes_are_capped(self, loaded: CollectionConfig) -> None:
        for spec in loaded.sources:
            if isinstance(spec, SitemapSource):
                assert 0 < spec.max_metadata_probes <= 50


class TestPerSourceCaps:
    """The item caps the collection block sets, kept from the collector it replaced."""

    def test_a_source_without_an_override_uses_the_shared_cap(
        self, loaded: CollectionConfig
    ) -> None:
        spec = loaded.source("simonw")
        assert spec.max_items is None
        assert loaded.items_for(spec) == loaded.max_items_per_source


class TestValidation:
    def test_an_unknown_connector_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source() | {"connector": "scrape"})

    def test_an_unknown_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(selector=".post"))

    def test_an_unknown_top_level_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(), retries=3)

    def test_an_unknown_http_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(), http=s.HTTP_SETTINGS | {"verify_tls": False})

    def test_an_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source() | {"kind": "survey"})

    def test_a_source_id_outside_the_contract_pattern_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source("Reddit:LocalLLaMA"))

    def test_duplicate_source_ids_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source("simonw"), s.feed_source("simonw", url="https://b.example/f"))

    def test_an_empty_registry_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            s.config()

    @pytest.mark.parametrize("url", ["", "not a url", "javascript:alert(1)", "file:///etc/passwd"])
    def test_a_non_http_source_url_is_rejected(self, url: str) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(url=url))

    @pytest.mark.parametrize("probes", [-1, 51])
    def test_the_probe_cap_has_bounds(self, probes: int) -> None:
        with pytest.raises(ValidationError):
            s.config(s.sitemap_source(max_metadata_probes=probes))

    @pytest.mark.parametrize("repo", ["anthropics", "anthropics/claude code", "/x", "a/b/c"])
    def test_a_malformed_repository_is_rejected(self, repo: str) -> None:
        with pytest.raises(ValidationError):
            s.config(
                {
                    "id": "gh_x",
                    "kind": "fixed_watch",
                    "connector": "gh_releases",
                    "enabled": True,
                    "repo": repo,
                }
            )

    def test_hacker_news_needs_at_least_one_keyword(self) -> None:
        with pytest.raises(ValidationError):
            s.config(
                {
                    "id": "hackernews",
                    "kind": "discovery",
                    "connector": "hackernews",
                    "enabled": True,
                    "keywords": [],
                }
            )

    @pytest.mark.parametrize("days", [0, 91])
    def test_the_window_has_bounds(self, days: int) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(), window_days=days)

    @pytest.mark.parametrize("days", [0, 91])
    def test_a_feed_window_has_the_same_bounds(self, days: int) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(window_days=days))

    @pytest.mark.parametrize("categories", [[], ["Product", " "]])
    def test_a_category_list_names_at_least_one_category(self, categories: list[str]) -> None:
        with pytest.raises(ValidationError):
            s.config(s.feed_source(categories=categories))

    def test_categories_are_a_feed_setting(self) -> None:
        with pytest.raises(ValidationError):
            s.config(s.sitemap_source(categories=["Product"]))

    def test_the_discovery_search_keeps_its_own_hours_instead(self) -> None:
        with pytest.raises(ValidationError):
            s.config(
                {
                    "id": "hackernews",
                    "kind": "discovery",
                    "connector": "hackernews",
                    "enabled": True,
                    "keywords": ["llm"],
                    "window_days": 30,
                }
            )

    def test_a_disabled_source_is_kept_but_not_enabled(self) -> None:
        config = s.config(s.feed_source(enabled=False), s.feed_source("boristane", url="https://b.example/f"))
        assert len(config.sources) == 2
        assert [spec.id for spec in config.enabled_sources] == ["boristane"]

    def test_the_config_is_frozen(self) -> None:
        config = s.config(s.feed_source())
        with pytest.raises(ValidationError):
            config.window_days = 3

    def test_a_missing_collection_block_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"sources": {}}), encoding="utf-8")
        with pytest.raises(KeyError):
            load_collection_config(path)

    def test_an_unknown_source_id_raises(self, loaded: CollectionConfig) -> None:
        with pytest.raises(KeyError):
            loaded.source("nope")


class TestConfigPath:
    """The caller names the config file. There is no default.

    The default was `Path(__file__).parents[2] / "config.json"`,
    which only resolves inside the source tree; from an installed wheel it
    pointed outside site-packages. No test called `load_collection_config()`
    without a path, so the breakage was invisible. #10 passes the path.
    """

    def test_a_path_is_required(self) -> None:
        with pytest.raises(TypeError):
            load_collection_config()

    def test_the_package_exports_no_default_path(self) -> None:
        assert not hasattr(__import__("agent_daily_digest.collect.run", fromlist=["run"]), "DEFAULT_CONFIG_PATH")
