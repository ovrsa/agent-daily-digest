"""Print the normalization output for every HTML fixture, as one JSON document.

Run as a subprocess by `test_normalize_determinism.py` under different
`PYTHONHASHSEED` values. Not collected by pytest (the name does not start with
`test_`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from normalize_helpers import HTML_DIR, StubFetcher, page  # noqa: E402

from agent_daily_digest.contracts.articles import CollectedItem, ProcessedState
from agent_daily_digest.normalize import normalize_items
from agent_daily_digest.state import ProcessedIndex, dump_state_json, record_article

FIXTURES = sorted(p.stem for p in HTML_DIR.glob("*.html"))
SEEN_AT = "2026-09-17T06:00:00+00:00"


def main() -> int:
    items = []
    results = {}
    for index, name in enumerate(FIXTURES, start=1):
        url = f"https://example.com/posts/{name}"
        items.append(
            CollectedItem.model_validate(
                {
                    "article_id": f"a{index:03d}",
                    "source_id": "simonw",
                    "source_kind": "fixed_watch",
                    "title": f"Fixture {name}",
                    "url": url,
                    "published_at": "2026-09-17T06:00:00+09:00",
                    "feed_summary": "Short synthetic feed summary.",
                }
            )
        )
        results[url] = page(name, url=url)

    outcomes = normalize_items(
        tuple(items), fetch=StubFetcher(results), index=ProcessedIndex.from_state(ProcessedState())
    )

    state = ProcessedState()
    payload = []
    for result in outcomes:
        payload.append(
            {
                "article_id": result.article_id,
                "outcome": json.loads(result.outcome.model_dump_json()),
                "article": json.loads(result.article.model_dump_json()) if result.article else None,
                "failure": json.loads(result.failure.model_dump_json()) if result.failure else None,
            }
        )
        if result.article is not None:
            state = record_article(state, result.article, "included", seen_at=SEEN_AT)

    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    sys.stdout.write("\n")
    sys.stdout.write(dump_state_json(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
