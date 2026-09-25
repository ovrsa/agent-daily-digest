"""The registry that ships in `config/config.json`, run end to end on recorded responses.

The feeds added in #31 were recorded on 2026-09-25 and cut to their first two
entries, with long bodies shortened; `<source_id>_feed.xml` holds each one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from digest_collect import CollectionConfig, HttpResponse, collect_all, load_collection_config
from digest_contracts import SourceFetchStatus, SourceKind

import collect_support as s

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "config.json"

REQUIRED_BLOGS = (
    "simonw",
    "boristane",
    "nyosegawa",
    "addyosmani",
    "claude_blog",
    "anthropic_engineering",
)

QUIET_IN_RECORDING = ("boristane", "nyosegawa", "addyosmani", "anthropic_engineering")
"""Required blogs with no post in the week before the fixed clock."""

RECORDED_FEEDS = (
    "goedecke",
    "syu-m-5151",
    "mizchi",
    "ronacher",
    "breunig",
    "harper",
    "hamel",
    "pragmatic_engineer",
    "karpathy",
    "mitchellh",
    "ghuntley",
    "yegge",
    "lilianweng",
    "eugeneyan",
    "litt",
    "huyenchip",
    "steipete",
    "arxiv_surveys",
)
"""Sources added in #31, each replayed from its own recording."""

EVERY_ENTRY = 3650
"""A window in days wide enough to keep every recorded entry."""

BY_URL = {
    "https://simonwillison.net/atom/everything/": "simonwillison_atom.xml",
    "https://boristane.com/rss.xml": "boristane_rss.xml",
    "https://nyosegawa.com/feed.xml": "nyosegawa_rss.xml",
    "https://addyosmani.com/rss.xml": "addyosmani_rss.xml",
    "https://buttondown.com/ainews/rss": "ai_news_smol_rss.xml",
    "https://www.latent.space/feed": "latent_space_rss.xml",
    "https://www.interconnects.ai/feed": "interconnects_rss.xml",
    s.CLAUDE_SITEMAP: "claude_sitemap.xml",
    s.ANTHROPIC_SITEMAP: "anthropic_sitemap.xml",
}

BY_PREFIX = (
    ("https://hn.algolia.com/", "hn_algolia.json"),
    ("https://claude.com/blog/", "claude_post_head.html"),
    ("https://www.anthropic.com/engineering/", "anthropic_post_head.html"),
)


class RegistryFetcher:
    """Answers every URL the shipped registry asks for, from a recording."""

    def __init__(self) -> None:
        self.requested: list[str] = []
        shipped = load_collection_config(CONFIG_PATH)
        self.by_url = BY_URL | {
            shipped.source(source_id).url: f"{source_id.replace('-', '_')}_feed.xml"
            for source_id in RECORDED_FEEDS
        }

    def get(self, url: str, *, accept: str = "*/*") -> HttpResponse:
        self.requested.append(url)
        name = self.by_url.get(url)
        if name is None:
            for prefix, candidate in BY_PREFIX:
                if url.startswith(prefix):
                    name = candidate
                    break
        if name is None:
            raise AssertionError(f"the registry asked for an unrecorded URL: {url}")
        return HttpResponse(url=url, status=200, body=s.read(name))


@pytest.fixture(scope="module")
def shipped() -> CollectionConfig:
    return load_collection_config(CONFIG_PATH)


@pytest.fixture(scope="module")
def report(shipped: CollectionConfig):
    return collect_all(shipped, fetcher=RegistryFetcher(), now=s.NOW)


class TestShippedRegistryRuns:
    def test_every_enabled_source_produces_a_result(self, shipped, report) -> None:
        assert len(report.results) == len(shipped.enabled_sources)

    def test_no_source_fails(self, report) -> None:
        assert report.failed_source_ids == ()

    def test_the_run_produces_candidates(self, report) -> None:
        assert len(report.items) > 20

    def test_every_item_belongs_to_a_registered_source(self, shipped, report) -> None:
        registered = {spec.id for spec in shipped.sources}
        assert {item.source_id for item in report.items} <= registered

    def test_article_ids_are_unique_across_the_whole_run(self, report) -> None:
        ids = [item.article_id for item in report.items]
        assert len(set(ids)) == len(ids)

    def test_metrics_cover_every_source(self, report) -> None:
        assert len(report.metrics) == len(report.results)
        assert len(report.probes) == len(report.results)


class TestRequiredBlogs:
    @pytest.mark.parametrize("source_id", REQUIRED_BLOGS)
    def test_each_required_blog_is_checked_and_succeeds(self, report, source_id: str) -> None:
        result = report.result(source_id)
        assert result.status is SourceFetchStatus.SUCCEEDED
        assert result.source_kind is SourceKind.FIXED_WATCH

    @pytest.mark.parametrize("source_id", [b for b in REQUIRED_BLOGS if b not in QUIET_IN_RECORDING])
    def test_each_required_blog_with_recent_posts_yields_candidates(
        self, report, source_id: str
    ) -> None:
        assert report.result(source_id).item_count > 0

    @pytest.mark.parametrize("source_id", QUIET_IN_RECORDING)
    def test_a_quiet_blog_is_empty_only_because_of_the_window(self, shipped, source_id: str) -> None:
        # Their newest recorded posts are more than `window_days` before the
        # fixed clock. Widening the window brings the same posts back.
        assert report_with_window(shipped, 7).result(source_id).item_count == 0
        wide = report_with_window(shipped, 90).result(source_id)
        assert wide.item_count > 0
        assert wide.status is SourceFetchStatus.SUCCEEDED

    def test_the_engineering_blog_is_empty_only_because_of_the_window(self, shipped) -> None:
        # The recorded sitemap's newest engineering entry is 2026-08-10, more
        # than `window_days` before the fixed clock. Widening the window brings
        # the same source's candidates back, so the emptiness is the window.
        assert report_with_window(shipped, 7).result("anthropic_engineering").item_count == 0
        wide = report_with_window(shipped, 90).result("anthropic_engineering")
        assert wide.item_count > 0
        assert wide.status is SourceFetchStatus.SUCCEEDED

    def test_the_sitemap_blogs_get_their_titles_from_the_probe(self, shipped) -> None:
        wide = report_with_window(shipped, 90)
        for source_id in ("claude_blog", "anthropic_engineering"):
            titles = [item.title for item in wide.result(source_id).items if item.title]
            assert titles

    def test_the_probe_cap_holds_for_both_sitemap_sources(self, shipped) -> None:
        wide = report_with_window(shipped, 90)
        for source_id in ("claude_blog", "anthropic_engineering"):
            cap = shipped.source(source_id).max_metadata_probes
            assert wide.probe(source_id).attempted <= cap


class TestAddedSources:
    @pytest.mark.parametrize("source_id", RECORDED_FEEDS)
    def test_each_added_source_reads_every_entry_of_its_recording(
        self, shipped, source_id: str
    ) -> None:
        result = report_with_window(shipped, EVERY_ENTRY).result(source_id)
        assert result.status is SourceFetchStatus.SUCCEEDED
        assert result.item_count == 2

    def test_the_arxiv_surveys_are_a_discovery_path(self, report) -> None:
        assert report.result("arxiv_surveys").source_kind is SourceKind.DISCOVERY


class TestPartialFailure:
    def test_one_required_blog_failing_leaves_the_rest_collected(self, shipped) -> None:
        class BoristaneDown(RegistryFetcher):
            def get(self, url, *, accept="*/*"):
                if url == "https://boristane.com/rss.xml":
                    raise s.timeout()
                return super().get(url, accept=accept)

        report = collect_all(shipped, fetcher=BoristaneDown(), now=s.NOW)
        assert report.failed_source_ids == ("boristane",)
        assert report.result("boristane").failure.kind.value == "timeout"
        for source_id in REQUIRED_BLOGS:
            if source_id != "boristane":
                assert report.result(source_id).status is SourceFetchStatus.SUCCEEDED

    def test_every_source_down_still_reports_one_row_each(self, shipped) -> None:
        class Down:
            def get(self, url, *, accept="*/*"):
                raise s.timeout()

        report = collect_all(shipped, fetcher=Down(), now=s.NOW)
        assert len(report.failed_source_ids) == len(shipped.enabled_sources)
        assert all(result.failure is not None for result in report.results)


def report_with_window(shipped: CollectionConfig, days: int):
    widened = shipped.model_copy(update={"window_days": days})
    return collect_all(widened, fetcher=RegistryFetcher(), now=s.NOW)
