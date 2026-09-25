"""Publication-date parsing for the `required_fields` gate.

Three formats are accepted, which together cover the collected sources:

- ISO 8601 / RFC 3339, as Atom feeds, JSON APIs and `sitemap.xml` `lastmod` emit it
- RFC 5322 (RFC 2822), as RSS `pubDate` emits it
- an English month name, as the `ld+json` `datePublished` of claude.com/blog
  emits it (`Jun 02, 2026`). That blog is one the design always checks, and it
  publishes no feed, so rejecting the format would drop every article on it.

The month names are matched against a table written out here, not through
`strptime("%b")`, whose month names follow `LC_TIME` and would make the gate
depend on the environment the run happens to start in.

The ISO parser is written out here rather than delegated to
`datetime.fromisoformat`, whose accepted syntax widened in 3.11: the harness has
to behave the same on 3.10 and 3.12, and a date the gate accepts on one of them
must not be rejected on the other.

A value without an offset is read as UTC. RFC 5322 already defines `-0000` that
way, and the alternative - dropping the article - loses more than an offset a
publisher did not state.
"""

from __future__ import annotations

import datetime as dt
import re
from email.utils import parsedate_to_datetime
from enum import Enum

from ._text import is_blank, strip_invisible

UTC = dt.timezone.utc

_ISO = re.compile(
    r"""
    ^
    (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})
    (?:
        [Tt ]
        (?P<hour>\d{2}):(?P<minute>\d{2})
        (?::(?P<second>\d{2})(?:\.(?P<fraction>\d+))?)?
        (?P<offset>[Zz]|[+-]\d{2}(?::?\d{2})?)?
    )?
    $
    """,
    re.VERBOSE,
)


_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_MONTH_NAME = re.compile(r"^(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})$")


class PublishedAtProblem(Enum):
    """Why a publication date is unusable, in the two shapes the gate records."""

    MISSING = "missing"
    """Blank: the source carried no date, sent as `""`, as whitespace or not at all."""
    UNPARSEABLE = "unparseable"
    """Present but no accepted format reads it."""


class PublishedAtRejected(ValueError):
    def __init__(self, problem: PublishedAtProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


def parse_published_at(raw: str | None) -> dt.datetime:
    """Return the timezone-aware publication date, or raise `PublishedAtRejected`."""
    if is_blank(raw):
        raise PublishedAtRejected(PublishedAtProblem.MISSING)
    assert raw is not None
    value = strip_invisible(raw).strip()

    parsed = _parse_iso(value) or _parse_rfc5322(value) or _parse_month_name(value)
    if parsed is None:
        raise PublishedAtRejected(PublishedAtProblem.UNPARSEABLE)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_iso(value: str) -> dt.datetime | None:
    match = _ISO.match(value)
    if match is None:
        return None
    parts = match.groupdict()
    second = int(parts["second"] or 0)
    # RFC 3339 allows a leap second. Python's `datetime` does not, and clamping
    # keeps the value within the same minute.
    second = min(second, 59)
    fraction = (parts["fraction"] or "")[:6].ljust(6, "0") if parts["fraction"] else "0"
    try:
        return dt.datetime(
            int(parts["year"]),
            int(parts["month"]),
            int(parts["day"]),
            int(parts["hour"] or 0),
            int(parts["minute"] or 0),
            second,
            int(fraction),
            tzinfo=_offset(parts["offset"]),
        )
    except ValueError:
        return None


def _offset(raw: str | None) -> dt.timezone | None:
    if raw is None:
        return None
    if raw in ("Z", "z"):
        return UTC
    digits = raw[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:] or 0)
    if hours > 23 or minutes > 59:
        raise ValueError("offset out of range")
    delta = dt.timedelta(hours=hours, minutes=minutes)
    return UTC if raw[0] == "-" and not delta else dt.timezone(-delta if raw[0] == "-" else delta)


def _parse_rfc5322(value: str) -> dt.datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _parse_month_name(value: str) -> dt.datetime | None:
    match = _MONTH_NAME.match(value)
    if match is None:
        return None
    month = _MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    try:
        return dt.datetime(int(match.group("year")), month, int(match.group("day")), tzinfo=UTC)
    except ValueError:
        return None
