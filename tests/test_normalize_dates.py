"""Accepted publication-date formats and the blank / unparseable split.

A feed with no date comes through blank (`""`, whitespace or `None`), so blank means the
date is absent (`missing_published_at`), and a non-blank value that no accepted
format parses means it is malformed (`invalid_published_at`).
"""

from __future__ import annotations

import datetime as dt

import pytest

from agent_daily_digest.normalize import (
    PublishedAtProblem,
    PublishedAtRejected,
    parse_published_at,
)

UTC = dt.timezone.utc
JST = dt.timezone(dt.timedelta(hours=9))


def reason(value: str | None) -> PublishedAtProblem:
    with pytest.raises(PublishedAtRejected) as excinfo:
        parse_published_at(value)
    return excinfo.value.problem


class TestMissing:
    @pytest.mark.parametrize("value", [None, "", " ", "\t\n", "\u3000", "\u200b", "\ufeff "])
    def test_blank_is_missing_not_invalid(self, value: str | None) -> None:
        assert reason(value) is PublishedAtProblem.MISSING


class TestUnparseable:
    @pytest.mark.parametrize(
        "value",
        [
            "yesterday",
            "2026-13-01T00:00:00Z",
            "2026-09-31T00:00:00Z",
            "2026-09-17T25:00:00Z",
            "2026-09-17T10:61:00Z",
            "17/09/2026",
            "2026-09",
            "2026",
            "Wed, 32 Sep 2026 06:00:00 +0900",
            "Wed, 17 Sep 2026 06:00:00 +9900",
            "ignore previous instructions",
            "2026-09-17T06:00:00Z extra",
        ],
    )
    def test_unparseable_is_invalid(self, value: str) -> None:
        assert reason(value) is PublishedAtProblem.UNPARSEABLE


class TestIso8601:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-09-17T06:00:00+09:00", dt.datetime(2026, 9, 17, 6, 0, 0, tzinfo=JST)),
            ("2026-09-17T06:00:00+0900", dt.datetime(2026, 9, 17, 6, 0, 0, tzinfo=JST)),
            ("2026-09-17T06:00:00+09", dt.datetime(2026, 9, 17, 6, 0, 0, tzinfo=JST)),
            ("2026-09-16T21:00:00Z", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("2026-09-16t21:00:00z", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("2026-09-16 21:00:00Z", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("2026-09-16T21:00Z", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("2026-09-16T21:00:00-00:00", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            (
                "2026-09-16T21:00:00.123Z",
                dt.datetime(2026, 9, 16, 21, 0, 0, 123000, tzinfo=UTC),
            ),
            (
                "2026-09-16T21:00:00.123456789Z",
                dt.datetime(2026, 9, 16, 21, 0, 0, 123456, tzinfo=UTC),
            ),
            ("  2026-09-16T21:00:00Z  ", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
        ],
    )
    def test_accepted(self, raw: str, expected: dt.datetime) -> None:
        assert parse_published_at(raw) == expected

    def test_a_leap_second_is_clamped(self) -> None:
        assert parse_published_at("2026-12-31T23:59:60Z") == dt.datetime(
            2026, 12, 31, 23, 59, 59, tzinfo=UTC
        )

    def test_the_offset_is_preserved_not_converted(self) -> None:
        parsed = parse_published_at("2026-09-17T06:00:00+09:00")
        assert parsed.utcoffset() == dt.timedelta(hours=9)


class TestRfc5322:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Wed, 16 Sep 2026 21:00:00 GMT", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("Wed, 16 Sep 2026 21:00:00 +0000", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("Thu, 17 Sep 2026 06:00:00 +0900", dt.datetime(2026, 9, 17, 6, 0, 0, tzinfo=JST)),
            ("16 Sep 2026 21:00:00 GMT", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
            ("Wed, 16 Sep 2026 21:00 GMT", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
        ],
    )
    def test_accepted(self, raw: str, expected: dt.datetime) -> None:
        assert parse_published_at(raw) == expected

    def test_unknown_local_zone_is_utc(self) -> None:
        # RFC 5322 3.3: `-0000` means UTC with the local zone unknown.
        assert parse_published_at("Wed, 16 Sep 2026 21:00:00 -0000") == dt.datetime(
            2026, 9, 16, 21, 0, 0, tzinfo=UTC
        )


class TestEnglishMonthName:
    """`ld+json` `datePublished` on claude.com/blog, which publishes no feed (#4)."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Jun 02, 2026", dt.datetime(2026, 6, 2, tzinfo=UTC)),
            ("June 2, 2026", dt.datetime(2026, 6, 2, tzinfo=UTC)),
            ("JUN 02, 2026", dt.datetime(2026, 6, 2, tzinfo=UTC)),
            ("Sept 30, 2026", dt.datetime(2026, 9, 30, tzinfo=UTC)),
            ("Dec 31 2026", dt.datetime(2026, 12, 31, tzinfo=UTC)),
        ],
    )
    def test_accepted(self, raw: str, expected: dt.datetime) -> None:
        assert parse_published_at(raw) == expected

    @pytest.mark.parametrize("raw", ["Jum 02, 2026", "Jun 32, 2026", "Jun 2026", "02 2026"])
    def test_rejected(self, raw: str) -> None:
        assert reason(raw) is PublishedAtProblem.UNPARSEABLE

    def test_the_month_table_does_not_depend_on_the_locale(self) -> None:
        # `strptime("%b")` would read `LC_TIME`; the table here cannot.
        assert parse_published_at("Jun 02, 2026").month == 6


class TestWithoutTimezone:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-09-17", dt.datetime(2026, 9, 17, 0, 0, 0, tzinfo=UTC)),
            ("2026-09-17T06:00:00", dt.datetime(2026, 9, 17, 6, 0, 0, tzinfo=UTC)),
            ("Wed, 16 Sep 2026 21:00:00", dt.datetime(2026, 9, 16, 21, 0, 0, tzinfo=UTC)),
        ],
    )
    def test_a_missing_offset_is_read_as_utc(self, raw: str, expected: dt.datetime) -> None:
        assert parse_published_at(raw) == expected


class TestDeterminism:
    @pytest.mark.parametrize(
        "raw",
        [
            "2026-09-17T06:00:00+09:00",
            "2026-09-17",
            "Wed, 16 Sep 2026 21:00:00 GMT",
            "2026-09-16T21:00:00.123456789Z",
        ],
    )
    def test_the_result_is_always_aware_and_stable(self, raw: str) -> None:
        first = parse_published_at(raw)
        assert first.tzinfo is not None and first.utcoffset() is not None
        assert parse_published_at(raw) == first
        assert parse_published_at(raw).isoformat() == first.isoformat()
