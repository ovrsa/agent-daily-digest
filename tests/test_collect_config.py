"""The registry in `config/config.json`, and that it has not drifted from the legacy keys."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from digest_collect import CollectionConfig, SitemapSource, load_collection_config
from digest_contracts import SourceKind

import collect_support as s

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "config.json"

REQUIRED_BLOG_URLS = {
    "claude_blog": "https://claude.com/blog/",
    "boristane": "https://boristane.com/",
    "nyosegawa": "https://nyosegawa.com/",
    "anthropic_engineering": "https://www.anthropic.com/engineering/",
    "simonw": "https://simonwillison.net/",
    "addyosmani": "https://addyosmani.com/",
}
"""The six blogs #1 says to check every run, by the source that covers each."""

LEGACY_TO_NEW = {
    "simonw": ("simonw",),
    "ai_news_smol": ("ai_news_smol",),
    "latent_space": ("latent_space",),
    "interconnects": ("interconnects",),
    "hf_papers": ("hf_papers",),
    "hackernews": ("hackernews",),
    "reddit": (
        "reddit_localllama",
        "reddit_claudeai",
        "reddit_cursor",
        "reddit_machinelearning",
    ),
    "gh_releases": (
        "gh_anthropics_claude-code",
        "gh_aider-ai_aider",
        "gh_cline_cline",
        "gh_continuedev_continue",
        "gh_openai_codex",
        "gh_princeton-nlp_swe-agent",
        "gh_block_goose",
    ),
}


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


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

    def test_discovery_sources_are_the_hacker_news_and_reddit_paths(
        self, loaded: CollectionConfig
    ) -> None:
        discovery = {s.id for s in loaded.sources if s.kind is SourceKind.DISCOVERY}
        assert discovery == {"hackernews", *LEGACY_TO_NEW["reddit"]}

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

    def test_metadata_probes_are_capped(self, loaded: CollectionConfig) -> None:
        for spec in loaded.sources:
            if isinstance(spec, SitemapSource):
                assert 0 < spec.max_metadata_probes <= 50


class TestLegacyRegistryHasNotDrifted:
    """`src/fetch.py` still reads the old keys until #11 retires it."""

    def test_every_legacy_toggle_maps_to_new_sources(self, raw: dict) -> None:
        assert set(raw["sources"]) == set(LEGACY_TO_NEW)

    def test_an_enabled_legacy_toggle_has_every_mapped_source_enabled(
        self, raw: dict, loaded: CollectionConfig
    ) -> None:
        for legacy_id, new_ids in LEGACY_TO_NEW.items():
            for new_id in new_ids:
                assert loaded.source(new_id).enabled is bool(raw["sources"][legacy_id])

    def test_the_reddit_subs_still_match(self, raw: dict, loaded: CollectionConfig) -> None:
        assert {f"reddit_{sub.lower()}" for sub in raw["reddit_subs"]} == set(
            LEGACY_TO_NEW["reddit"]
        )
        for sub in raw["reddit_subs"]:
            assert f"/r/{sub}/" in loaded.source(f"reddit_{sub.lower()}").url

    def test_every_legacy_repo_has_a_release_source(
        self, raw: dict, loaded: CollectionConfig
    ) -> None:
        repos = {
            spec.repo for spec in loaded.sources if spec.connector == "gh_releases"
        }
        assert repos == set(raw["gh_repos"])

    def test_the_hacker_news_keywords_still_match(
        self, raw: dict, loaded: CollectionConfig
    ) -> None:
        assert list(loaded.source("hackernews").keywords) == raw["hackernews_keywords"]

    def test_the_item_cap_still_matches(self, raw: dict, loaded: CollectionConfig) -> None:
        assert loaded.max_items_per_source == raw["max_items_per_source"]

    def test_each_subreddit_keeps_the_share_the_legacy_fetcher_gave_it(
        self, raw: dict, loaded: CollectionConfig
    ) -> None:
        # src/fetch.py: per_sub = max(3, limit // len(subs))
        share = max(3, raw["max_items_per_source"] // len(raw["reddit_subs"]))
        for source_id in LEGACY_TO_NEW["reddit"]:
            assert loaded.items_for(loaded.source(source_id)) == share

    def test_each_repository_keeps_the_two_releases_the_legacy_fetcher_took(
        self, loaded: CollectionConfig
    ) -> None:
        # src/fetch.py: fetch_gh_releases(..., limit_per_repo=2)
        for spec in loaded.sources:
            if spec.connector == "gh_releases":
                assert loaded.items_for(spec) == 2

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
