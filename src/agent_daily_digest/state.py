"""The Git-tracked processing state.

`ProcessedRecord` holds four fields and nothing else, so an article body, a
model's input or output and a secret cannot reach the repository through this
file. The contract rejects unknown keys, and the tests read the written file
back to show that what is absent from the shape is absent from the bytes.

The file is written in a fixed order with a fixed layout so that a run that
changes nothing produces no diff.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from agent_daily_digest.contracts import (
    ContentHash,
    Decision,
    HttpUrlStr,
    NormalizedArticle,
    ProcessedRecord,
    ProcessedState,
)


DEFAULT_STATE_PATH = Path("state/processed.json")
"""Where the pipeline keeps the state unless #10 points it elsewhere."""


@dataclass(frozen=True)
class ProcessedIndex:
    """Lookup over the state, for the `not_previously_processed` gate."""

    urls: frozenset[str]
    content_hashes: frozenset[str]

    @classmethod
    def from_state(cls, state: ProcessedState) -> ProcessedIndex:
        return cls(
            urls=frozenset(record.canonical_url for record in state.records),
            content_hashes=frozenset(record.content_hash for record in state.records),
        )

    @classmethod
    def empty(cls) -> ProcessedIndex:
        return cls(urls=frozenset(), content_hashes=frozenset())

    def has_url(self, canonical_url: HttpUrlStr) -> bool:
        return canonical_url in self.urls

    def has_content_hash(self, content_hash: ContentHash) -> bool:
        return content_hash in self.content_hashes


def load_state(path: Path | str = DEFAULT_STATE_PATH) -> ProcessedState:
    """Read the state file. A missing file is an empty state, not an error."""
    file = Path(path)
    if not file.exists():
        return ProcessedState()
    return ProcessedState.model_validate_json(file.read_text(encoding="utf-8"))


def dump_state_json(state: ProcessedState) -> str:
    """Serialize the state in the exact form `save_state` writes."""
    payload = state.model_dump(mode="json")
    payload["records"] = sorted(
        payload["records"], key=lambda record: (record["canonical_url"], record["content_hash"])
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_state(path: Path | str, state: ProcessedState) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(dump_state_json(state), encoding="utf-8")


def record_article(
    state: ProcessedState,
    article: NormalizedArticle,
    decision: Decision | str,
    *,
    seen_at: dt.datetime | str,
) -> ProcessedState:
    """Return a new state that records this article's canonical URL, hash and decision.

    An article already in the state keeps its first-seen time; only the decision
    changes. A changed body produces a different hash, which is a new record.
    """
    key = (article.canonical_url, article.content_hash)
    records = []
    replaced = False
    for record in state.records:
        if (record.canonical_url, record.content_hash) == key:
            records.append(
                ProcessedRecord.model_validate(
                    {
                        "canonical_url": record.canonical_url,
                        "first_seen_at": record.first_seen_at,
                        "content_hash": record.content_hash,
                        "last_decision": decision,
                    }
                )
            )
            replaced = True
        else:
            records.append(record)
    if not replaced:
        records.append(
            ProcessedRecord.model_validate(
                {
                    "canonical_url": article.canonical_url,
                    "first_seen_at": seen_at,
                    "content_hash": article.content_hash,
                    "last_decision": decision,
                }
            )
        )
    return ProcessedState(schema_version=state.schema_version, records=tuple(records))
