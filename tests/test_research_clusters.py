"""Clusters decide where research is spent: once per topic, on the article closest to the source."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from research_support import PUBLISHED, article

from agent_daily_digest.config import load_research_budget
from agent_daily_digest.contracts.articles import SourceKind
from agent_daily_digest.research.run import cluster_articles, resolve_links


def test_an_article_linking_to_another_joins_its_cluster() -> None:
    post = article("p1", title="A new harness design", url="https://blog.example/harness")
    thread = article("h1", source_id="hackernews", kind=SourceKind.DISCOVERY, title="Discussion", url="https://news.example/1")
    (cluster,) = cluster_articles([thread, post], {"h1": ("https://blog.example/harness",)})
    assert cluster.representative.article_id == "p1" and [a.article_id for a in cluster.supporting] == ["h1"]


def test_articles_of_one_source_linking_each_other_stay_apart() -> None:
    # A blog's sidebar links its other posts whatever their topic.
    release = article("s1", title="llm 0.36", url="https://blog.example/2026/llm-0-36")
    prices = article("s2", title="The frontier model price war", url="https://blog.example/2026/prices")
    clusters = cluster_articles([release, prices], {"s1": (prices.canonical_url,), "s2": (release.canonical_url,)})
    assert [(c.representative.article_id, c.supporting) for c in clusters] == [("s1", ()), ("s2", ())]


def test_articles_of_one_source_still_join_on_similar_titles() -> None:
    a = article("a1", title="Claude Code hooks for permission gating")
    b = article("b1", title="Permission gating with Claude Code hooks")
    (cluster,) = cluster_articles([a, b])
    assert [x.article_id for x in cluster.supporting] == ["b1"]


def test_similar_titles_join_and_different_titles_do_not() -> None:
    a = article("a1", title="Claude Code hooks for permission gating")
    b = article("b1", title="Permission gating with Claude Code hooks", source_id="reddit_claudeai", kind=SourceKind.DISCOVERY)
    c = article("c1", title="Evaluating retrieval pipelines")
    clusters = cluster_articles([a, b, c])
    assert [(x.representative.article_id, tuple(s.article_id for s in x.supporting)) for x in clusters] == [("a1", ("b1",)), ("c1", ())]


def test_the_representative_is_official_then_fixed_watch_then_earliest() -> None:
    title = "Harness retry budget explained"
    discovery = article("d1", title=title, source_id="hackernews", kind=SourceKind.DISCOVERY, published_at=PUBLISHED - timedelta(days=1))
    expert = article("e1", title=title, source_id="simonw")
    official = article("o1", title=title, source_id="gh_anthropics_claude-code", published_at=PUBLISHED + timedelta(hours=5))
    (cluster,) = cluster_articles([discovery, expert, official])
    assert cluster.representative.article_id == "o1"
    assert [a.article_id for a in cluster.supporting] == ["e1", "d1"]


def test_earliest_is_compared_across_time_zones() -> None:
    title = "Harness retry budget explained"
    tokyo = article("t1", title=title, published_at=datetime(2026, 9, 24, 8, 0, tzinfo=timezone(timedelta(hours=9))))  # 23:00Z the day before
    utc = article("u1", title=title, published_at=datetime(2026, 9, 23, 23, 30, tzinfo=timezone.utc))
    (cluster,) = cluster_articles([utc, tokyo])
    assert cluster.representative.article_id == "t1"


def test_links_are_resolved_against_the_article_and_filtered_to_http() -> None:
    post = article("a1", url="https://blog.example/posts/x")
    links = resolve_links(post, ("/docs/setup", "#top", "mailto:a@b.c", "https://blog.example/posts/x#frag", "https://GitHub.com/o/r?utm_source=x", "/docs/setup"))
    assert links == ("https://blog.example/docs/setup", "https://github.com/o/r")


def test_the_budget_is_read_from_the_config(tmp_path) -> None:
    assert load_research_budget("config.json").max_rounds == 2
    path = tmp_path / "c.json"
    path.write_text('{"research": {"max_rounds": 9}}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_research_budget(path)


def test_the_public_names_resolve_and_importing_does_not_load_the_sdk() -> None:
    code = "import sys, agent_daily_digest.research.run, agent_daily_digest.research.evidence; print('\\n'.join(sys.modules))"
    loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert "claude_agent_sdk" not in loaded
