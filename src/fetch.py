#!/usr/bin/env python3
"""LLM/Coding Agent daily digest fetcher.

Fetches items from 8 high-signal sources and writes a unified JSON
to stdout (or to --out). Standard library only - no pip install needed.

Each source returns a list of dicts with at minimum:
    {"source", "title", "url", "published", "score", "summary_hint"}
A failing source logs a warning and returns []; other sources keep working.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

UA = "llm-daily-digest:v1.0 (personal local digest)"
TIMEOUT = 20
SSL_CTX = ssl.create_default_context()
UTC = dt.timezone.utc


def _http_get(url: str, *, accept: str = "*/*", ua: str | None = None) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": ua or UA, "Accept": accept}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        return r.read()


def _http_json(url: str) -> object:
    return json.loads(_http_get(url, accept="application/json").decode("utf-8"))


def _log(msg: str) -> None:
    print(f"[fetch] {msg}", file=sys.stderr)


def _parse_iso(s: str | None) -> str:
    if not s:
        return ""
    return s.strip()


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:400]


def fetch_simonw(limit: int) -> list[dict]:
    url = "https://simonwillison.net/atom/everything/"
    try:
        raw = _http_get(url, accept="application/atom+xml")
    except Exception as e:
        _log(f"simonw failed: {e}")
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        _log(f"simonw parse error: {e}")
        return []
    items = []
    for entry in root.findall("a:entry", ns)[:limit]:
        title = entry.findtext("a:title", default="", namespaces=ns)
        link_el = entry.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        pub = entry.findtext("a:updated", default="", namespaces=ns)
        content = (entry.findtext("a:content", default="", namespaces=ns)
                   or entry.findtext("a:summary", default="", namespaces=ns))
        items.append({
            "source": "simonw",
            "title": (title or "").strip(),
            "url": link or "",
            "published": _parse_iso(pub),
            "score": None,
            "summary_hint": _strip_tags(content or ""),
        })
    return items


def _fetch_rss(url: str, source: str, limit: int) -> list[dict]:
    try:
        raw = _http_get(url, accept="application/rss+xml")
    except Exception as e:
        _log(f"{source} failed: {e}")
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        _log(f"{source} parse error: {e}")
        return []
    items = []
    for it in root.findall(".//item")[:limit]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        desc = it.findtext("description") or ""
        items.append({
            "source": source,
            "title": title,
            "url": link,
            "published": _parse_iso(pub),
            "score": None,
            "summary_hint": _strip_tags(desc),
        })
    return items


def fetch_latent_space(limit: int) -> list[dict]:
    return _fetch_rss("https://www.latent.space/feed", "latent_space", limit)


def fetch_interconnects(limit: int) -> list[dict]:
    return _fetch_rss("https://www.interconnects.ai/feed", "interconnects", limit)


def fetch_ai_news_smol(limit: int) -> list[dict]:
    for url in (
        "https://buttondown.com/ainews/rss",
        "https://news.smol.ai/rss",
    ):
        items = _fetch_rss(url, "ai_news_smol", limit)
        if items:
            return items
    return []


def fetch_hf_papers(limit: int) -> list[dict]:
    today = dt.date.today().isoformat()
    url = f"https://huggingface.co/api/daily_papers?date={today}"
    data = None
    try:
        data = _http_json(url)
    except Exception as e:
        _log(f"hf_papers {today} failed: {e}")
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        try:
            data = _http_json(
                f"https://huggingface.co/api/daily_papers?date={yesterday}"
            )
        except Exception as e2:
            _log(f"hf_papers {yesterday} failed: {e2}")
            return []
    if not isinstance(data, list):
        return []
    items = []
    for entry in data[:limit]:
        paper = entry.get("paper", {}) or {}
        pid = paper.get("id") or entry.get("id") or ""
        title = (entry.get("title") or paper.get("title") or "").strip()
        abstract = paper.get("summary") or paper.get("abstract") or ""
        upvotes = paper.get("upvotes") or entry.get("upvotes")
        items.append({
            "source": "hf_papers",
            "title": title,
            "url": f"https://huggingface.co/papers/{pid}" if pid else "",
            "published": entry.get("publishedAt") or "",
            "score": upvotes,
            "summary_hint": _strip_tags(abstract),
        })
    return items


def fetch_hackernews(keywords: list[str], limit: int) -> list[dict]:
    since = int((dt.datetime.now(UTC).replace(tzinfo=None) - dt.timedelta(days=1)).timestamp())
    seen = set()
    items: list[dict] = []
    for kw in keywords:
        query = urllib.parse.quote(kw)
        url = (
            f"https://hn.algolia.com/api/v1/search"
            f"?query={query}&tags=story&numericFilters="
            f"created_at_i>{since},points>30&hitsPerPage=20"
        )
        try:
            data = _http_json(url)
        except Exception as e:
            _log(f"hn keyword '{kw}' failed: {e}")
            continue
        for hit in data.get("hits", []):
            oid = hit.get("objectID")
            if oid in seen:
                continue
            seen.add(oid)
            items.append({
                "source": "hackernews",
                "title": (hit.get("title") or "").strip(),
                "url": (hit.get("url")
                        or f"https://news.ycombinator.com/item?id={oid}"),
                "published": hit.get("created_at") or "",
                "score": hit.get("points"),
                "summary_hint": (hit.get("story_text") or "")[:400],
                "hn_id": oid,
                "matched_keyword": kw,
            })
    items.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return items[:limit]


def fetch_reddit(subs: list[str], limit: int) -> list[dict]:
    """Reddit top/day via Atom RSS (JSON endpoint is anti-bot blocked).

    Atom does not expose upvote score, but RSS order = ranking, so we
    keep insertion order and assign descending pseudo-scores per sub.
    """
    out: list[dict] = []
    per_sub = max(3, limit // max(1, len(subs)))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for sub in subs:
        url = (f"https://www.reddit.com/r/{sub}/top.rss"
               f"?t=day&limit={per_sub}")
        try:
            raw = _http_get(url, accept="application/atom+xml")
        except Exception as e:
            _log(f"reddit r/{sub} failed: {e}")
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            _log(f"reddit r/{sub} parse error: {e}")
            continue
        rank = 0
        for entry in root.findall("a:entry", ns)[:per_sub]:
            rank += 1
            title = entry.findtext("a:title", default="", namespaces=ns) or ""
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            pub = entry.findtext("a:published", default="", namespaces=ns) or \
                  entry.findtext("a:updated", default="", namespaces=ns) or ""
            content = entry.findtext("a:content", default="",
                                     namespaces=ns) or ""
            out.append({
                "source": f"reddit:{sub}",
                "title": title.strip(),
                "url": link or "",
                "published": _parse_iso(pub),
                "score": per_sub - rank + 1,  # rank-derived pseudo-score
                "rank_in_sub": rank,
                "summary_hint": _strip_tags(content),
            })
    out.sort(key=lambda x: (x.get("rank_in_sub") or 99))
    return out[:limit]


def fetch_gh_releases(repos: list[str], limit_per_repo: int = 2) -> list[dict]:
    out: list[dict] = []
    cutoff = dt.datetime.now(UTC).replace(tzinfo=None) - dt.timedelta(days=7)
    for repo in repos:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=5"
        try:
            data = _http_json(url)
        except Exception as e:
            _log(f"gh_releases {repo} failed: {e}")
            continue
        if not isinstance(data, list):
            continue
        added = 0
        for rel in data:
            published = rel.get("published_at") or rel.get("created_at") or ""
            try:
                pub_dt = dt.datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pub_dt = None
            if pub_dt and pub_dt < cutoff:
                continue
            out.append({
                "source": f"gh:{repo}",
                "title": f"{repo} {rel.get('tag_name') or rel.get('name', '')}",
                "url": (rel.get("html_url")
                        or f"https://github.com/{repo}/releases"),
                "published": published,
                "score": None,
                "summary_hint": (rel.get("body") or "")[:600],
            })
            added += 1
            if added >= limit_per_repo:
                break
    return out


def load_config(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def gather(cfg: dict) -> dict:
    limit = int(cfg.get("max_items_per_source", 12))
    src = cfg.get("sources", {})
    bucket: dict[str, list[dict]] = {}

    if src.get("simonw"):
        bucket["simonw"] = fetch_simonw(limit)
    if src.get("ai_news_smol"):
        bucket["ai_news_smol"] = fetch_ai_news_smol(limit)
    if src.get("latent_space"):
        bucket["latent_space"] = fetch_latent_space(limit)
    if src.get("interconnects"):
        bucket["interconnects"] = fetch_interconnects(limit)
    if src.get("hf_papers"):
        bucket["hf_papers"] = fetch_hf_papers(limit)
    if src.get("hackernews"):
        bucket["hackernews"] = fetch_hackernews(
            cfg.get("hackernews_keywords", []), limit
        )
    if src.get("reddit"):
        bucket["reddit"] = fetch_reddit(cfg.get("reddit_subs", []), limit)
    if src.get("gh_releases"):
        bucket["gh_releases"] = fetch_gh_releases(cfg.get("gh_repos", []))

    counts = {k: len(v) for k, v in bucket.items()}
    _log(f"counts: {counts}")
    return {
        "generated_at": dt.datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        "date": dt.date.today().isoformat(),
        "sources": bucket,
        "counts": counts,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config",
                   default=str(Path(__file__).parent / "config.json"))
    p.add_argument("--out", default="-",
                   help="Output path or '-' for stdout")
    args = p.parse_args()

    cfg = load_config(Path(args.config))
    payload = gather(cfg)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        _log(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
