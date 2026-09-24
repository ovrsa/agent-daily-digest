"""The eight sources `src/fetch.py` collects today still come out the same.

`src/fetch.py` is what the routine runs; #11 retires it. Until then the new
layer has to agree with it on the sources they share. Both are driven with the
same recorded bytes, and the comparison is on what identifies a candidate:
its title and its URL.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from digest_collect import CollectContext, ProbeRecorder
from digest_collect.config import SOURCE_SPEC_ADAPTER
from digest_collect.connectors import CONNECTORS

import collect_support as s

LEGACY_PATH = Path(__file__).resolve().parents[1] / "src" / "fetch.py"
pytestmark = pytest.mark.skipif(not LEGACY_PATH.exists(), reason="src/fetch.py is not in this tree")


@pytest.fixture(scope="module")
def legacy():
    spec = importlib.util.spec_from_file_location("legacy_fetch", LEGACY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["legacy_fetch"] = module
    spec.loader.exec_module(module)
    return module


def serve(legacy, body: bytes):
    """Point every legacy request at one recorded response."""
    legacy._http_get = lambda url, **_kw: body


def identity(items) -> set[tuple[str | None, str | None]]:
    out = set()
    for item in items:
        if isinstance(item, dict):
            out.add((item["title"] or None, item["url"] or None))
        else:
            out.add((item.title, item.url))
    return out


def collect_new(source: dict, body: bytes, *, window_days: int = 7, max_items: int = 12):
    parsed = SOURCE_SPEC_ADAPTER.validate_python(source)
    ctx = CollectContext(
        fetcher=s.FixtureFetcher({}, default=body),
        now=s.NOW,
        window=dt.timedelta(days=window_days),
        max_items=max_items,
        probe=ProbeRecorder(),
    )
    return CONNECTORS[parsed.connector](parsed, ctx)


class TestFeedParity:
    @pytest.mark.parametrize(
        ("source_id", "fixture", "url"),
        [
            ("latent_space", "latent_space_rss.xml", "https://www.latent.space/feed"),
            ("interconnects", "interconnects_rss.xml", "https://www.interconnects.ai/feed"),
            ("ai_news_smol", "ai_news_smol_rss.xml", "https://buttondown.com/ainews/rss"),
        ],
    )
    def test_rss_sources_produce_the_same_candidates(
        self, legacy, source_id: str, fixture: str, url: str
    ) -> None:
        body = s.read(fixture)
        serve(legacy, body)
        old = legacy._fetch_rss(url, source_id, 12)
        new = collect_new(s.feed_source(source_id, url=url), body)
        assert identity(new) == identity(old)

    def test_simon_willisons_atom_feed_produces_the_same_candidates(self, legacy) -> None:
        body = s.read("simonwillison_atom.xml")
        serve(legacy, body)
        old = legacy.fetch_simonw(12)
        new = collect_new(s.feed_source("simonw", url="https://simonwillison.net/atom/everything/"), body)
        assert identity(new) == identity(old)

    def test_reddit_produces_the_same_candidates(self, legacy) -> None:
        body = s.read("reddit_localllama_atom.xml")
        serve(legacy, body)
        old = legacy.fetch_reddit(["LocalLLaMA", "ClaudeAI", "cursor", "MachineLearning"], 12)
        new = collect_new(
            s.feed_source("reddit_localllama", url="https://www.reddit.com/r/LocalLLaMA/top.rss")
            | {"kind": "discovery"},
            body,
            max_items=3,
        )
        # The legacy fetcher pulls every subreddit from one recording here, so
        # compare against the share one subreddit contributes.
        assert identity(new) <= identity(old)
        assert len(new) == 3


class TestJsonParity:
    def test_hugging_face_papers_produce_the_same_candidates(self, legacy) -> None:
        body = s.read("hf_papers.json")
        legacy._http_json = lambda url: __import__("json").loads(body)
        old = legacy.fetch_hf_papers(12)
        new = collect_new(
            {
                "id": "hf_papers",
                "kind": "fixed_watch",
                "connector": "hf_papers",
                "enabled": True,
                "url": "https://huggingface.co/api/daily_papers",
            },
            body,
        )
        assert identity(new) == identity(old)

    def test_hacker_news_produces_the_same_candidates(self, legacy) -> None:
        body = s.read("hn_algolia.json")
        legacy._http_json = lambda url: __import__("json").loads(body)
        old = legacy.fetch_hackernews(["coding agent"], 12)
        new = collect_new(
            {
                "id": "hackernews",
                "kind": "discovery",
                "connector": "hackernews",
                "enabled": True,
                "url": "https://hn.algolia.com/api/v1/search",
                "keywords": ["coding agent"],
                "min_points": 30,
            },
            body,
        )
        assert identity(new) == identity(old)

    def test_github_releases_produce_the_same_candidates(self, legacy, monkeypatch) -> None:
        body = s.read("gh_releases.json")
        legacy._http_json = lambda url: __import__("json").loads(body)

        class FixtureDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return s.NOW.astimezone(tz) if tz is not None else s.NOW.replace(tzinfo=None)

        # The legacy fetcher uses the wall clock. Pin it to the recording's
        # date so this comparison still tests parity after the fixture ages.
        monkeypatch.setattr(
            legacy, "dt", SimpleNamespace(datetime=FixtureDateTime, timedelta=dt.timedelta)
        )
        old = legacy.fetch_gh_releases(["anthropics/claude-code"], limit_per_repo=2)
        new = collect_new(
            {
                "id": "gh_anthropics_claude-code",
                "kind": "fixed_watch",
                "connector": "gh_releases",
                "enabled": True,
                "repo": "anthropics/claude-code",
            },
            body,
            max_items=2,
        )
        assert identity(new) == identity(old)


class TestKnownDifferences:
    """Where the two deliberately disagree, so the change is visible."""

    def test_source_ids_replace_the_legacy_colon_form(self, legacy) -> None:
        body = s.read("reddit_localllama_atom.xml")
        serve(legacy, body)
        old = legacy.fetch_reddit(["LocalLLaMA"], 12)
        assert {item["source"] for item in old} == {"reddit:LocalLLaMA"}
        new = collect_new(
            s.feed_source("reddit_localllama", url="https://www.reddit.com/r/LocalLLaMA/top.rss")
            | {"kind": "discovery"},
            body,
        )
        # `SourceId` in the contracts allows no colon and no upper case.
        assert {item.source_id for item in new} == {"reddit_localllama"}

    def test_the_new_layer_drops_the_score_field(self, legacy) -> None:
        body = s.read("hn_algolia.json")
        legacy._http_json = lambda url: __import__("json").loads(body)
        assert "score" in legacy.fetch_hackernews(["coding agent"], 12)[0]
        # Ranking stays inside the connector; `CollectedItem` has no score.
        new = collect_new(
            {
                "id": "hackernews",
                "kind": "discovery",
                "connector": "hackernews",
                "enabled": True,
                "url": "https://hn.algolia.com/api/v1/search",
                "keywords": ["coding agent"],
                "min_points": 30,
            },
            body,
        )
        assert not hasattr(new[0], "score")
