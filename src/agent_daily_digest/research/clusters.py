"""Grouping articles on the same topic, and choosing which one is researched.

Research runs once per cluster, on the article closest to the primary source;
the others are carried as its supporting articles. This is a candidate grouping
by fixed rules. Whether two articles really say the same thing is the Selector's
judgement (`DuplicateGroup`), so a cluster only decides where research is spent.

Two articles are one cluster when one links to the other's canonical URL, or
when their titles share most of their words.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timezone

from agent_daily_digest.contracts import NormalizedArticle, SourceKind

TITLE_SIMILARITY = 0.6
"""Jaccard similarity of title words at or above which two titles name one topic."""

MIN_SHARED_TITLE_WORDS = 3

OFFICIAL_SOURCE_PREFIXES = ("claude_blog", "anthropic_engineering", "gh_")
"""Sources that publish first-hand: the vendor's own posts and release notes."""

_WORD = re.compile(r"[0-9a-z]+|[぀-ヿ一-鿿]+")
_STOP = frozenset({"a", "an", "the", "of", "to", "in", "on", "for", "and", "with", "how", "we", "is", "our", "your"})


@dataclass(frozen=True)
class Cluster:
    representative: NormalizedArticle
    supporting: tuple[NormalizedArticle, ...] = ()


def cluster_articles(
    articles: Sequence[NormalizedArticle],
    links: Mapping[str, Sequence[str]] | None = None,
) -> tuple[Cluster, ...]:
    """Clusters in the order their representatives first appear in `articles`.

    `links` maps an article id to the canonical URLs its page links to.
    """
    links = links or {}
    parent = list(range(len(articles)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_url = {article.canonical_url: i for i, article in enumerate(articles)}
    for i, article in enumerate(articles):
        for url in links.get(article.article_id, ()):
            j = by_url.get(url)
            if j is not None and j != i:
                union(i, j)
    words = [_title_words(article.title) for article in articles]
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            shared = words[i] & words[j]
            union_size = len(words[i] | words[j])
            if len(shared) >= MIN_SHARED_TITLE_WORDS and union_size and len(shared) / union_size >= TITLE_SIMILARITY:
                union(i, j)

    groups: dict[int, list[NormalizedArticle]] = {}
    for i, article in enumerate(articles):
        groups.setdefault(find(i), []).append(article)
    clusters = []
    for root in sorted(groups):
        members = sorted(groups[root], key=_priority)
        clusters.append(Cluster(representative=members[0], supporting=tuple(members[1:])))
    return tuple(clusters)


def _priority(article: NormalizedArticle) -> tuple[int, str, str]:
    if article.source_id.startswith(OFFICIAL_SOURCE_PREFIXES):
        rank = 0
    elif article.source_kind is SourceKind.FIXED_WATCH:
        rank = 1
    else:
        rank = 2
    # Earliest first, compared in one zone: offsets differ between feeds.
    return (rank, article.published_at.astimezone(timezone.utc).isoformat(), article.article_id)


def _title_words(title: str) -> frozenset[str]:
    text = unicodedata.normalize("NFKC", title).lower()
    return frozenset(word for word in _WORD.findall(text) if word not in _STOP)
