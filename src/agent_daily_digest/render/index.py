"""The `digests/README.md` index of recent digests.

The index is derived from the files in `digests/`, never accumulated, so a
deleted or added file is reflected on the next run without a separate state.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from agent_daily_digest.render.digest import (
    IndexMarkerError,
)

INDEX_BEGIN = "<!-- INDEX:START -->"
INDEX_END = "<!-- INDEX:END -->"
INDEX_MAX_ENTRIES = 30
INDEX_EMPTY_TEXT = "_（まだダイジェストはありません。最初の実行で生成されます。）_"
README_FILENAME = "README.md"


def render_index(dates: Iterable[date]) -> str:
    """The index body: the newest `INDEX_MAX_ENTRIES` dates, one link each."""
    recent = sorted(set(dates), reverse=True)[:INDEX_MAX_ENTRIES]
    if not recent:
        return INDEX_EMPTY_TEXT
    return "\n".join(f"- [{day.isoformat()}](./{day.isoformat()}.md)" for day in recent)


def update_index(readme_text: str, dates: Iterable[date]) -> str:
    """Replace the text between the index markers, leaving the rest untouched."""
    begin = readme_text.find(INDEX_BEGIN)
    end = readme_text.find(INDEX_END)
    if begin < 0 or end < 0:
        raise IndexMarkerError(f"{README_FILENAME} needs {INDEX_BEGIN} and {INDEX_END}")
    if end < begin + len(INDEX_BEGIN):
        raise IndexMarkerError(f"{INDEX_END} must come after {INDEX_BEGIN}")
    head = readme_text[: begin + len(INDEX_BEGIN)]
    tail = readme_text[end:]
    return f"{head}\n{render_index(dates)}\n{tail}"
