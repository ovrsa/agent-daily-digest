"""What run metrics are allowed to carry, and the check that runs before they are written.

Run metrics live outside Git, but they are read at the weekly review and a
summary of them is posted as a commit comment. Three kinds of text must never
reach them: article bodies, model input or output, and secrets. The contracts
keep bodies out by shape; the free-text fields that remain (`ErrorRecord.detail`,
`decision_reason`) are where a leak can happen, so this module does two things:

- `safe_detail` turns an exception into a short operator note that never quotes
  the exception message verbatim when that message can carry model output
- `find_leaks` scans a serialized record for secrets and for any long run of
  text taken from a body or a prompt the caller marked as sensitive
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from digest_contracts import ERROR_DETAIL_MAX_CHARS

LEAK_WINDOW_CHARS = 64
"""A run of this many characters shared with a sensitive text counts as a copy.

Short enough to catch a quoted sentence, long enough that a product name or a
URL an article also mentions does not trip it. Whitespace is collapsed first.
"""

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("oauth_env", re.compile(r"CLAUDE_CODE_OAUTH_TOKEN\s*=\s*\S+")),
    ("url_credentials", re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")),
)
"""Credentials this project can hold: model auth, `gh` auth, and URLs with userinfo."""

_REDACTED = "<redacted>"


def redact_secrets(text: str) -> str:
    """Replace every match of `SECRET_PATTERNS` with a fixed marker."""
    for _name, pattern in SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def safe_detail(text: str | None) -> str | None:
    """A detail string fit for `ErrorRecord.detail`: redacted, one line, capped."""
    if text is None:
        return None
    collapsed = " ".join(redact_secrets(text).split())
    return collapsed[:ERROR_DETAIL_MAX_CHARS] or None


def describe_exception(exc: BaseException) -> str:
    """Name an exception without quoting its message.

    SDK and parser exceptions put the offending input in their message: a JSON
    decode error quotes the model's output, a process error quotes the CLI's
    stderr. The class name and, when present, a numeric status are enough to act on.
    """
    parts = [type(exc).__name__]
    for attribute in ("status_code", "exit_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            parts.append(f"{attribute}={value}")
    return " ".join(parts)


@dataclass(frozen=True)
class Leak:
    """Where a forbidden text was found. The text itself is not repeated."""

    location: str
    kind: str
    """`secret:<pattern name>` or `sensitive_text`."""


class MetricsLeakError(ValueError):
    """A record was about to be written with a secret or a copied body in it."""

    def __init__(self, leaks: tuple[Leak, ...]) -> None:
        self.leaks = leaks
        super().__init__(
            "refusing to write metrics: " + ", ".join(f"{leak.location} ({leak.kind})" for leak in leaks)
        )


def find_leaks(
    payload: Any,
    sensitive: Iterable[str] = (),
    *,
    window: int = LEAK_WINDOW_CHARS,
) -> tuple[Leak, ...]:
    """Every string in `payload` that holds a secret or a copy of a sensitive text.

    `payload` is JSON-shaped data (what `model_dump(mode="json")` returns).
    """
    strings = list(_strings(payload, "$"))
    leaks: list[Leak] = []
    for location, value in strings:
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                leaks.append(Leak(location=location, kind=f"secret:{name}"))
                break

    windows: dict[str, str] = {}
    for location, value in strings:
        text = _collapse(value)
        for start in range(0, max(0, len(text) - window + 1)):
            windows.setdefault(text[start : start + window], location)
    if windows:
        flagged: set[str] = set()
        for source in sensitive:
            text = _collapse(source)
            for start in range(0, max(0, len(text) - window + 1)):
                location = windows.get(text[start : start + window])
                if location is not None and location not in flagged:
                    flagged.add(location)
                    leaks.append(Leak(location=location, kind="sensitive_text"))
    return tuple(leaks)


def _strings(value: Any, location: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{location}[{index}]")


def _collapse(text: str) -> str:
    return " ".join(text.split())
