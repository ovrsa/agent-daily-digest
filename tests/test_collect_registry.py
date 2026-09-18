"""The registry that ships in `config/config.json`, run end to end on recorded responses.

Sources that share an endpoint shape share a recording: the seven GitHub
repositories replay one releases payload and the four subreddits one Atom
feed, because only the path differs between them.
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
    ("https://www.reddit.com/r/", "reddit_localllama_atom.xml"),
    ("https://api.github.com/repos/", "gh_releases.json"),
    ("https://hn.algolia.com/", "hn_algolia.json"),
    ("https://huggingface.co/api/daily_papers?date=2026-09-17", "hf_papers.json"),
    ("https://claude.com/blog/", "claude_post_head.html"),
    ("https://www.anthropic.com/engineering/", "anthropic_post_head.html"),
)


class RegistryFetcher:
    """Answers every URL the shipped registry asks for, from a recording."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def get(self, url: str, *, accept: str = "*/*") -> HttpResponse:
        self.requested.append(url)
        name = BY_URL.get(url)
        if name is None:
            for prefix, candidate in BY_PREFIX:
                if url.startswith(prefix):
                    name = candidate
                    break
        if name is None:
            # `?date=<today>` answers 400 until the day's list is published.
            if url.startswith("https://huggingface.co/api/daily_papers"):
                raise s.http_error(400, url)
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

    @pytest.mark.parametrize("source_id", [b for b in REQUIRED_BLOGS if b != "anthropic_engineering"])
    def test_each_required_blog_with_recent_posts_yields_candidates(
        self, report, source_id: str
    ) -> None:
        assert report.result(source_id).item_count > 0

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
