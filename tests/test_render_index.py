"""The `digests/README.md` index and the write path that refreshes it."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

import render_factories as f
from agent_daily_digest.contracts import SelectorOutput
from agent_daily_digest.render import (
    INDEX_BEGIN,
    INDEX_EMPTY_TEXT,
    INDEX_END,
    INDEX_MAX_ENTRIES,
    ForbiddenArtifactError,
    IndexMarkerError,
    list_digest_dates,
    render_index,
    update_index,
    write_digest,
)


def digests_dir(tmp_path: Path, *filenames: str) -> Path:
    directory = tmp_path / "digests"
    directory.mkdir()
    (directory / "README.md").write_text(f.README_TEMPLATE, encoding="utf-8")
    for name in filenames:
        (directory / name).write_text("# placeholder\n", encoding="utf-8")
    return directory


def index_body(readme_text: str) -> str:
    return readme_text.split(INDEX_BEGIN)[1].split(INDEX_END)[0].strip("\n")


def test_the_index_lists_the_newest_first() -> None:
    body = render_index([date(2026, 9, 16), date(2026, 9, 18), date(2026, 9, 17)])
    assert body.splitlines() == [
        "- [2026-09-18](./2026-09-18.md)",
        "- [2026-09-17](./2026-09-17.md)",
        "- [2026-09-16](./2026-09-16.md)",
    ]


def test_the_index_keeps_the_most_recent_thirty() -> None:
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(45)]
    lines = render_index(days).splitlines()
    assert len(lines) == INDEX_MAX_ENTRIES == 30
    assert lines[0] == f"- [{days[-1].isoformat()}](./{days[-1].isoformat()}.md)"
    assert lines[-1] == f"- [{days[-30].isoformat()}](./{days[-30].isoformat()}.md)"


def test_a_repeated_date_is_listed_once() -> None:
    body = render_index([date(2026, 9, 18), date(2026, 9, 18)])
    assert body.splitlines() == ["- [2026-09-18](./2026-09-18.md)"]


def test_an_empty_index_keeps_the_placeholder() -> None:
    assert render_index([]) == INDEX_EMPTY_TEXT


def test_only_the_marked_region_is_replaced() -> None:
    updated = update_index(f.README_TEMPLATE, [date(2026, 9, 18)])
    assert updated.startswith("# Digests\n\nアーカイブ。\n\n")
    assert updated.endswith(f"{INDEX_END}\n")
    assert index_body(updated) == "- [2026-09-18](./2026-09-18.md)"


def test_a_section_after_the_end_marker_is_kept() -> None:
    """`README_TEMPLATE` ends at the marker, so the tail is covered separately."""
    readme = (
        "# Digests\n"
        "\n"
        "アーカイブ。\n"
        "\n"
        f"{INDEX_BEGIN}\n"
        "古い一覧\n"
        f"{INDEX_END}\n"
        "\n"
        "## ライセンス\n"
        "\n"
        "本文は CC BY 4.0。\n"
    )
    updated = update_index(readme, [date(2026, 9, 18)])
    assert updated.endswith(f"{INDEX_END}\n\n## ライセンス\n\n本文は CC BY 4.0。\n")
    assert index_body(updated) == "- [2026-09-18](./2026-09-18.md)"


def test_updating_an_already_filled_index_replaces_it() -> None:
    once = update_index(f.README_TEMPLATE, [date(2026, 9, 17)])
    twice = update_index(once, [date(2026, 9, 18), date(2026, 9, 17)])
    assert index_body(twice).splitlines() == [
        "- [2026-09-18](./2026-09-18.md)",
        "- [2026-09-17](./2026-09-17.md)",
    ]


@pytest.mark.parametrize(
    "readme",
    [
        "# Digests\n",
        f"# Digests\n{INDEX_BEGIN}\n",
        f"# Digests\n{INDEX_END}\n",
        f"# Digests\n{INDEX_END}\n{INDEX_BEGIN}\n",
    ],
)
def test_a_readme_without_usable_markers_is_reported(readme: str) -> None:
    with pytest.raises(IndexMarkerError):
        update_index(readme, [date(2026, 9, 18)])


def test_the_dates_come_from_the_digest_files(tmp_path: Path) -> None:
    directory = digests_dir(
        tmp_path, "2026-09-16.md", "2026-09-18.md", "2026-09-17.md", "notes.md", "2026-13-40.md"
    )
    (directory / "2026-09-19.md").mkdir()
    assert list_digest_dates(directory) == (
        date(2026, 9, 18),
        date(2026, 9, 17),
        date(2026, 9, 16),
    )


def test_writing_a_digest_creates_the_file_and_refreshes_the_index(tmp_path: Path) -> None:
    directory = digests_dir(tmp_path, "2026-09-17.md")
    written = write_digest(f.selector_output(), f.articles(), f.DIGEST_DATE, directory)

    assert written == directory / "2026-09-18.md"
    assert written.read_bytes() == f.DIGEST_SNAPSHOT_PATH.read_bytes()
    assert index_body((directory / "README.md").read_text(encoding="utf-8")).splitlines() == [
        "- [2026-09-18](./2026-09-18.md)",
        "- [2026-09-17](./2026-09-17.md)",
    ]


def test_writing_the_same_digest_twice_leaves_the_same_bytes(tmp_path: Path) -> None:
    directory = digests_dir(tmp_path)
    write_digest(f.selector_output(), f.articles(), f.DIGEST_DATE, directory)
    first = {p.name: p.read_bytes() for p in sorted(directory.iterdir())}
    write_digest(f.selector_output(), f.articles(), f.DIGEST_DATE, directory)
    assert {p.name: p.read_bytes() for p in sorted(directory.iterdir())} == first


def test_a_run_that_adopted_nothing_writes_no_file(tmp_path: Path) -> None:
    directory = digests_dir(tmp_path)
    before = {p.name: p.read_bytes() for p in sorted(directory.iterdir())}

    assert write_digest(f.empty_selector_output(), f.articles(), f.DIGEST_DATE, directory) is None

    assert {p.name: p.read_bytes() for p in sorted(directory.iterdir())} == before
    assert index_body(before["README.md"].decode("utf-8")) == INDEX_EMPTY_TEXT


def test_a_rejected_digest_writes_nothing(tmp_path: Path) -> None:
    directory = digests_dir(tmp_path)
    before = {p.name: p.read_bytes() for p in sorted(directory.iterdir())}
    payload = f.selector_payload()
    payload["must_read"] = [
        f.included_payload(why_read="これは画期的な変更だ。"),
        *payload["must_read"][1:],
    ]

    with pytest.raises(ForbiddenArtifactError):
        write_digest(SelectorOutput.model_validate(payload), f.articles(), f.DIGEST_DATE, directory)

    assert {p.name: p.read_bytes() for p in sorted(directory.iterdir())} == before


def test_a_crlf_readme_is_written_back_with_lf(tmp_path: Path) -> None:
    """改行コードは LF に揃える。同じ入力を同じバイトにすることを優先した判断。"""
    directory = digests_dir(tmp_path)
    crlf = f.README_TEMPLATE.replace("\n", "\r\n")
    (directory / "README.md").write_bytes(crlf.encode("utf-8"))

    write_digest(f.selector_output(), f.articles(), f.DIGEST_DATE, directory)

    written = (directory / "README.md").read_bytes()
    assert b"\r\n" not in written
    assert index_body(written.decode("utf-8")) == "- [2026-09-18](./2026-09-18.md)"


def test_a_readme_without_markers_stops_the_write(tmp_path: Path) -> None:
    directory = digests_dir(tmp_path)
    (directory / "README.md").write_text("# Digests\n", encoding="utf-8")

    with pytest.raises(IndexMarkerError):
        write_digest(f.selector_output(), f.articles(), f.DIGEST_DATE, directory)

    assert not (directory / "2026-09-18.md").exists()
