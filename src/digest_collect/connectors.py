"""One function per collection method, each turning a response into `CollectedItem`s.

A connector may raise. The collector catches it, classifies it, and keeps
going with the other sources; nothing here decides a source's status.

Connectors stop at the identity of a candidate — URL, title, date, feed
summary. Fetching and normalizing the body is #5.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from digest_contracts import CollectedItem, SourceKind

from .config import (
    FeedSource,
    GitHubReleasesSource,
    HackerNewsSource,
    HuggingFacePapersSource,
    SitemapSource,
    SourceSpec,
)
from .transport import Fetcher, SitemapIndexError, parse_json, parse_xml

ATOM_NS = "http://www.w3.org/2005/Atom"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
DC_NS = "http://purl.org/dc/elements/1.1/"

SUMMARY_MAX_CHARS = 400
TITLE_MAX_CHARS = 500
HEAD_SCAN_MAX_CHARS = 200_000
"""How far into a page the metadata probe looks when it finds no `</head>`."""

_UTC = dt.timezone.utc


@dataclass
class ProbeRecorder:
    """Counts for the sitemap connector's extra metadata requests.

    The collector owns the instance and reads it whether the connector
    returned or raised, so a source that fails after probing still reports
    what it spent.
    """

    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped_over_cap: int = 0
    duration_ns: int = 0
    """Nanoseconds, so the collector can subtract it from the source's own
    elapsed time without a rounding step turning the difference negative."""

    @property
    def duration_ms(self) -> int:
        return self.duration_ns // 1_000_000


@dataclass(frozen=True)
class CollectContext:
    """Everything a connector needs that is not in its own spec."""

    fetcher: Fetcher
    now: dt.datetime
    window: dt.timedelta
    max_items: int
    probe: ProbeRecorder = field(default_factory=ProbeRecorder)
    known_urls: frozenset[str] = frozenset()
    """Canonical URLs already in the processing state.

    The collector never reads that state; #5 and #10 own it. Until #10 wires
    it up this stays empty, which only means more candidates reach the gates.
    """


Connector = Callable[[Any, CollectContext], tuple[CollectedItem, ...]]


def make_article_id(source_id: str, identity: str) -> str:
    """A stable id for one candidate.

    Derived from the source and the item's own identity (its URL when it has
    one), so the same post keeps the same id across runs and the join key in
    the metrics stays meaningful.
    """
    return hashlib.sha256(f"{source_id}\n{identity}".encode("utf-8")).hexdigest()[:32]


def _text(value: object) -> str | None:
    """Collapse a raw field into non-empty text, or nothing.

    Values that are not strings are stringified here rather than at the
    contract, which takes `published_at` as `str | None` (PR #13, N2).
    """
    if value is None or isinstance(value, bool):
        return None
    text = value.strip() if isinstance(value, str) else str(value)
    return text or None


def _strip_markup(value: str | None, limit: int = SUMMARY_MAX_CHARS) -> str | None:
    if not value:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    text = " ".join(text.split())
    return text[:limit] or None


def _first_text(element: ET.Element, *paths: str) -> str | None:
    for path in paths:
        found = element.findtext(path)
        if text := _text(found):
            return text
    return None


# --------------------------------------------------------------------------
# feed


def _feed_entries(root: ET.Element) -> Iterator[tuple[str | None, str | None, str | None, str | None]]:
    """Yield `(title, url, published, summary)` for RSS 2.0 and Atom alike."""
    items = root.findall(".//item")
    if items:
        for item in items:
            yield (
                _text(item.findtext("title")),
                _first_text(item, "link", "guid"),
                _first_text(item, "pubDate", f"{{{DC_NS}}}date"),
                _strip_markup(item.findtext("description")),
            )
        return
    for entry in root.findall(f".//{{{ATOM_NS}}}entry"):
        yield (
            _text(entry.findtext(f"{{{ATOM_NS}}}title")),
            _atom_link(entry),
            _first_text(entry, f"{{{ATOM_NS}}}published", f"{{{ATOM_NS}}}updated"),
            _strip_markup(
                entry.findtext(f"{{{ATOM_NS}}}summary") or entry.findtext(f"{{{ATOM_NS}}}content")
            ),
        )


def _feed_date(value: str | None) -> dt.datetime | None:
    """An Atom or Dublin Core date (W3C) or an RSS `pubDate` (RFC 822), or `None`."""
    parsed = parse_w3c_datetime(value)
    if parsed is not None or not value:
        return parsed
    try:
        parsed = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_UTC)


def _atom_link(entry: ET.Element) -> str | None:
    links = entry.findall(f"{{{ATOM_NS}}}link")
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return _text(link.get("href"))
    for link in links:
        if link.get("href"):
            return _text(link.get("href"))
    return _text(entry.findtext(f"{{{ATOM_NS}}}id"))


def collect_feed(spec: FeedSource, ctx: CollectContext) -> tuple[CollectedItem, ...]:
    response = ctx.fetcher.get(
        spec.url, accept="application/atom+xml, application/rss+xml, application/xml"
    )
    root = parse_xml(response.body)
    cutoff = ctx.now - ctx.window
    items = []
    for title, url, published, summary in _feed_entries(root):
        if url in ctx.known_urls:
            continue
        # A feed lists its latest entries whatever their age, so a quiet blog's
        # entries are months old. The window leaves them out as it does for the
        # sitemap and the releases; a date read nowhere here goes on to #5's gate.
        published_dt = _feed_date(published)
        if published_dt is not None and published_dt < cutoff:
            continue
        items.append(
            _item(spec, url=url, title=title, published_at=published, feed_summary=summary)
        )
        if len(items) >= ctx.max_items:
            break
    return tuple(items)


# --------------------------------------------------------------------------
# sitemap plus head-metadata probe


@dataclass(frozen=True)
class PageMetadata:
    title: str | None
    published_at: str | None
    description: str | None


_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')""")
_LD_JSON = re.compile(
    r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S
)
_TITLE_TAG = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_ARTICLE_TYPES = frozenset({"blogposting", "article", "newsarticle", "techarticle", "report"})


def read_head_metadata(body: bytes) -> PageMetadata:
    """Read title and publication date out of a page's `<head>`.

    Only the head is looked at, so this stays candidate identity and does not
    drift into body extraction (#5). Both pages that need it today keep their
    metadata there: `claude.com` a JSON-LD `BlogPosting`, `www.anthropic.com`
    an `og:title`.
    """
    text = body.decode("utf-8", errors="replace")
    end = text.lower().find("</head>")
    head = text[:end] if end >= 0 else text[:HEAD_SCAN_MAX_CHARS]

    title = published_at = description = None
    for block in _LD_JSON.findall(head):
        for node in _ld_nodes(block):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if not any(isinstance(t, str) and t.lower() in _ARTICLE_TYPES for t in types):
                continue
            title = title or _clean(node.get("headline"))
            published_at = published_at or _clean(node.get("datePublished"))
            description = description or _clean(node.get("description"))

    metas = _meta_attributes(head)
    title = title or _clean(metas.get("og:title")) or _clean(_first_group(_TITLE_TAG, head))
    published_at = published_at or _clean(
        metas.get("article:published_time") or metas.get("datePublished")
    )
    description = description or _clean(metas.get("og:description") or metas.get("description"))
    return PageMetadata(
        title=title,
        published_at=published_at,
        description=_strip_markup(description),
    )


def _ld_nodes(block: str) -> Iterator[Mapping[str, Any]]:
    try:
        data = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if isinstance(graph := node.get("@graph"), list):
                stack.extend(graph)
            yield node


def _meta_attributes(head: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in _META_TAG.findall(head):
        attrs = {
            name.lower(): (double if double is not None else single)
            for name, _, double, single in _ATTR.findall(tag)
        }
        key = attrs.get("property") or attrs.get("name")
        content = attrs.get("content")
        if key and content and key not in found:
            found[key] = content
    return found


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _text(" ".join(html.unescape(value).split())[:TITLE_MAX_CHARS])


@dataclass(frozen=True)
class _SitemapRow:
    loc: str
    lastmod_text: str
    lastmod: dt.datetime


def collect_sitemap(spec: SitemapSource, ctx: CollectContext) -> tuple[CollectedItem, ...]:
    response = ctx.fetcher.get(spec.url, accept="application/xml")
    root = parse_xml(response.body)
    if root.tag == f"{{{SITEMAP_NS}}}sitemapindex":
        # An index holds sitemaps, not URLs, so `.//{ns}url` would match
        # nothing and the source would report a clean zero. A site that
        # outgrows one sitemap must not read as a site with no new posts.
        raise SitemapIndexError("sitemap index, not a urlset; nested sitemaps are not followed")
    rows = _sitemap_rows(root, spec.url_prefix, ctx)
    rows.sort(key=lambda row: row.lastmod, reverse=True)
    rows = rows[: ctx.max_items]
    ctx.probe.skipped_over_cap = max(0, len(rows) - spec.max_metadata_probes)

    items = []
    for index, row in enumerate(rows):
        metadata = (
            _probe(row.loc, spec.url_prefix, ctx)
            if index < spec.max_metadata_probes
            else PageMetadata(None, None, None)
        )
        items.append(
            _item(
                spec,
                url=row.loc,
                title=metadata.title,
                # `lastmod` is the sitemap's last-modified date, not the
                # publication date. It is the only date `www.anthropic.com`
                # exposes; #5 owns parsing and may find a better one.
                published_at=metadata.published_at or row.lastmod_text,
                feed_summary=metadata.description,
            )
        )
    return tuple(items)


def _sitemap_rows(root: ET.Element, prefix: str, ctx: CollectContext) -> list[_SitemapRow]:
    cutoff = ctx.now - ctx.window
    rows = []
    for url in root.findall(f".//{{{SITEMAP_NS}}}url"):
        loc = _text(url.findtext(f"{{{SITEMAP_NS}}}loc"))
        lastmod_text = _text(url.findtext(f"{{{SITEMAP_NS}}}lastmod"))
        if not loc or not loc.startswith(prefix) or loc in ctx.known_urls:
            continue
        lastmod = parse_w3c_datetime(lastmod_text)
        if lastmod is None or lastmod < cutoff:
            continue
        rows.append(_SitemapRow(loc=loc, lastmod_text=lastmod_text or "", lastmod=lastmod))
    return rows


class _OffPrefixRedirect(Exception):
    """The probe was answered from outside the source's `url_prefix`.

    Never leaves `_probe`, which counts it as a failed probe.
    """


def _probe(url: str, prefix: str, ctx: CollectContext) -> PageMetadata:
    """Fetch one page's head metadata. A failure costs the title, not the item."""
    started = time.monotonic_ns()
    ctx.probe.attempted += 1
    try:
        response = ctx.fetcher.get(url, accept="text/html")
        if not response.url.startswith(prefix):
            # `<loc>` is untrusted input and `url_prefix` is the only bound on
            # it, but `urlopen` follows redirects. The page that answered is
            # not the one the prefix vouched for, so its title and description
            # are not this item's. The item keeps `row.loc` as its URL.
            raise _OffPrefixRedirect(f"answered from outside {prefix}")
        metadata = read_head_metadata(response.body)
    except Exception:
        ctx.probe.failed += 1
        # No guessed title from the slug: the item keeps `title=None` and #5's
        # `required_fields` gate records `missing_title`.
        return PageMetadata(None, None, None)
    else:
        ctx.probe.succeeded += 1
        return metadata
    finally:
        ctx.probe.duration_ns += time.monotonic_ns() - started


def parse_w3c_datetime(value: str | None) -> dt.datetime | None:
    """Parse a sitemap `<lastmod>`: a full timestamp or a bare date."""
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_UTC)


# --------------------------------------------------------------------------
# JSON APIs


def collect_hackernews(spec: HackerNewsSource, ctx: CollectContext) -> tuple[CollectedItem, ...]:
    since = int((ctx.now - dt.timedelta(hours=spec.window_hours)).timestamp())
    by_id: dict[str, tuple[CollectedItem, int]] = {}
    failures: list[Exception] = []
    for keyword in spec.keywords:
        # Algolia's default also searches the URL and story text and allows typos,
        # so a keyword finds stories whose titles never mention it.
        query = urllib.parse.urlencode(
            {
                "query": keyword,
                "tags": "story",
                "numericFilters": f"created_at_i>{since},points>{spec.min_points}",
                "hitsPerPage": spec.hits_per_keyword,
                "restrictSearchableAttributes": "title",
                "typoTolerance": "false",
            }
        )
        try:
            payload = parse_json(ctx.fetcher.get(f"{spec.url}?{query}", accept="application/json").body)
        except Exception as exc:  # one keyword, not the source
            failures.append(exc)
            continue
        for hit in payload.get("hits", []) if isinstance(payload, dict) else []:
            object_id = _text(hit.get("objectID"))
            if object_id is None or object_id in by_id:
                continue
            url = _text(hit.get("url")) or f"https://news.ycombinator.com/item?id={object_id}"
            if url in ctx.known_urls:
                continue
            points = hit.get("points") if isinstance(hit.get("points"), int) else 0
            by_id[object_id] = (
                _item(
                    spec,
                    url=url,
                    title=_text(hit.get("title")),
                    published_at=_text(hit.get("created_at")),
                    feed_summary=_strip_markup(hit.get("story_text")),
                    identity=f"hn:{object_id}",
                ),
                points,
            )
    if failures and not by_id:
        raise failures[0]
    ranked = sorted(by_id.values(), key=lambda pair: pair[1], reverse=True)
    return tuple(item for item, _ in ranked[: ctx.max_items])


def collect_hf_papers(
    spec: HuggingFacePapersSource, ctx: CollectContext
) -> tuple[CollectedItem, ...]:
    # `?date=<today>` answers 400 until the day's list is published, so the
    # previous day is the working request for most of the morning. The fallback
    # chain is kept from the retired `src/fetch.py`.
    today = ctx.now.astimezone(_UTC).date()
    last: Exception | None = None
    payload: Any = None
    for offset in (0, 1):
        day = (today - dt.timedelta(days=offset)).isoformat()
        try:
            payload = parse_json(ctx.fetcher.get(f"{spec.url}?date={day}", accept="application/json").body)
        except Exception as exc:
            last = exc
            continue
        if payload:
            break
    if payload is None:
        raise last if last is not None else RuntimeError("no response")
    if not isinstance(payload, list):
        return ()

    items = []
    for entry in payload[: ctx.max_items]:
        if not isinstance(entry, dict):
            continue
        paper = entry.get("paper") if isinstance(entry.get("paper"), dict) else {}
        paper_id = _text(paper.get("id")) or _text(entry.get("id"))
        if paper_id is None:
            continue
        url = f"https://huggingface.co/papers/{paper_id}"
        if url in ctx.known_urls:
            continue
        items.append(
            _item(
                spec,
                url=url,
                title=_text(entry.get("title")) or _text(paper.get("title")),
                published_at=_text(entry.get("publishedAt")) or _text(paper.get("publishedAt")),
                feed_summary=_strip_markup(paper.get("summary") or entry.get("summary")),
            )
        )
    return tuple(items)


def collect_gh_releases(spec: GitHubReleasesSource, ctx: CollectContext) -> tuple[CollectedItem, ...]:
    url = f"https://api.github.com/repos/{spec.repo}/releases?per_page={spec.releases_per_page}"
    payload = parse_json(ctx.fetcher.get(url, accept="application/vnd.github+json").body)
    if not isinstance(payload, list):
        return ()

    cutoff = ctx.now - ctx.window
    items = []
    for release in payload:
        if not isinstance(release, dict):
            continue
        published = _text(release.get("published_at")) or _text(release.get("created_at"))
        published_dt = parse_w3c_datetime(published)
        if published_dt is not None and published_dt < cutoff:
            continue
        tag = _text(release.get("tag_name")) or _text(release.get("name"))
        html_url = _text(release.get("html_url")) or f"https://github.com/{spec.repo}/releases"
        if html_url in ctx.known_urls:
            continue
        items.append(
            _item(
                spec,
                url=html_url,
                title=f"{spec.repo} {tag}" if tag else spec.repo,
                published_at=published,
                feed_summary=_strip_markup(release.get("body"), limit=600),
            )
        )
        if len(items) >= ctx.max_items:
            break
    return tuple(items)


# --------------------------------------------------------------------------

CONNECTORS: Mapping[str, Connector] = {
    "feed": collect_feed,
    "sitemap": collect_sitemap,
    "hackernews": collect_hackernews,
    "hf_papers": collect_hf_papers,
    "gh_releases": collect_gh_releases,
}


def _item(
    spec: SourceSpec,
    *,
    url: str | None,
    title: str | None,
    published_at: str | None,
    feed_summary: str | None,
    identity: str | None = None,
) -> CollectedItem:
    key = identity or url or f"{title}|{published_at}"
    return CollectedItem(
        article_id=make_article_id(spec.id, key),
        source_id=spec.id,
        source_kind=SourceKind(spec.kind),
        title=title,
        url=url,
        published_at=published_at,
        feed_summary=feed_summary,
    )


def dedupe(items: Iterable[CollectedItem]) -> tuple[CollectedItem, ...]:
    """Keep the first item per `article_id`.

    `SourceFetchResult` requires the ids within a source to be unique, and a
    feed that repeats a link would otherwise fail the whole source.
    """
    seen: set[str] = set()
    kept = []
    for item in items:
        if item.article_id in seen:
            continue
        seen.add(item.article_id)
        kept.append(item)
    return tuple(kept)
