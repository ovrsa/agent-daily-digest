"""Write a rendered digest and refresh the index, or write nothing."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

from digest_contracts import NormalizedArticle, SelectorOutput

from .digest import digest_filename, render_digest
from .index import README_FILENAME, list_digest_dates, update_index


def write_digest(
    selector_output: SelectorOutput,
    articles: Mapping[str, NormalizedArticle],
    digest_date: date,
    digests_dir: Path,
) -> Path | None:
    """Write `digests/<date>.md` and refresh the index. Return the path, or `None`.

    Nothing is written when the Selector adopted nothing, and nothing is
    written when rendering or the index update fails: every check runs before
    the first byte is written. The digest and the README are two writes, so a
    failure between them can still leave a digest whose index entry is missing.
    The index is derived from `digests/` on every run, so the next run repairs
    it; that is why it is derived rather than accumulated.

    The README is read with universal newlines and written back as UTF-8, so a
    CRLF file comes back as LF. That is intended: one newline convention is
    part of the same input producing the same bytes.

    Raises what `render_digest` raises, `IndexMarkerError` when the index
    markers are missing, and `FileNotFoundError` when `digests_dir` has no
    `README.md` to hold the index.
    """
    markdown = render_digest(selector_output, articles, digest_date)
    if markdown is None:
        return None

    readme_path = digests_dir / README_FILENAME
    readme_text = update_index(
        readme_path.read_text(encoding="utf-8"),
        (digest_date, *list_digest_dates(digests_dir)),
    )

    digest_path = digests_dir / digest_filename(digest_date)
    digest_path.write_bytes(markdown.encode("utf-8"))
    readme_path.write_bytes(readme_text.encode("utf-8"))
    return digest_path
