"""Machine check for the sentence-level artifacts the Design Doc bans.

The Output contract asks for a plain technical-editor register: name the actor,
and avoid abstract praise, mechanical two-sided contrasts, hype, decorative
emoji and full-width dashes. Those are properties of the Japanese prose the
Selector writes, so the renderer checks the Selector-authored spans and not the
article title or URL, which are quoted source text kept as they were published.

A propositional heading (命題型見出し) has no rule here: every heading the
renderer emits is either a fixed section name or a quoted article title, so
there is no generated heading a pattern could judge.

A finding stops the day's digest from being published, so a pattern earns its
place only when a match is far more often the artifact than ordinary prose.
Wordings that read as the artifact but also carry plain factual weight are left
to the Judge (#8) and the weekly human review, which comment rather than block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

MATCHED_MAX_CHARS = 40
"""Cap on the quoted span. The text is published anyway, so this is readability."""


class ForbiddenRule(str, Enum):
    """Identifier a caller can branch on. The wording of a pattern may change."""

    FULLWIDTH_DASH = "fullwidth_dash"
    DECORATIVE_EMOJI = "decorative_emoji"
    ABSENT_SUBJECT = "absent_subject"
    ABSTRACT_PRAISE = "abstract_praise"
    HYPE = "hype"
    MECHANICAL_CONTRAST = "mechanical_contrast"


RULE_DESCRIPTIONS: dict[ForbiddenRule, str] = {
    ForbiddenRule.FULLWIDTH_DASH: "全角ダッシュ。読点か括弧に置き換える",
    ForbiddenRule.DECORATIVE_EMOJI: "装飾絵文字",
    ForbiddenRule.ABSENT_SUBJECT: "主体の不在。誰がそう述べたかを書く",
    ForbiddenRule.ABSTRACT_PRAISE: "抽象的な称賛。本文で確認できる事実に置き換える",
    ForbiddenRule.HYPE: "過剰な煽り",
    ForbiddenRule.MECHANICAL_CONTRAST: "機械的な二項対比",
}

# U+2013 en dash is included: in Japanese technical prose it is an artifact, not
# a range separator. ASCII `-` and `--` are untouched, so front matter and
# thematic breaks are unaffected.
_DASHES = "–—―⸺⸻︱︲﹘－"

# `→` (U+2192) and the rest of the Arrows block are ordinary technical
# punctuation and stay out. The arrows inside U+2B00 are in: they render as
# emoji (`⬅` `⬆` `⬇`), which is the decorative use the contract bans.
#
# Check and ballot marks are cut out of the U+2600 block on purpose: `✓`
# (U+2713), `✔` (U+2714), `✗` (U+2717) and `✘` (U+2718) are how a
# comparison table is written in running prose, and a finding stops the day's
# publication. `✅` (U+2705) still matches, so the decorative use is covered.
_EMOJI = (
    "‼⁉"
    "☀-✒"  # U+2600-U+2712, stopping before `✓` and `✔`
    "✕✖"  # U+2715-U+2716, between the two cuts
    "✙-➿"  # U+2719-U+27BF, resuming after `✗` and `✘`
    "⬀-⯿"
    "〰〽"
    "️"
    "\U0001f000-\U0001f02f"
    "\U0001f0a0-\U0001f0ff"
    "\U0001f100-\U0001f1ff"
    "\U0001f300-\U0001faff"
)

_PATTERNS: dict[ForbiddenRule, re.Pattern[str]] = {
    ForbiddenRule.FULLWIDTH_DASH: re.compile(f"[{_DASHES}]+"),
    ForbiddenRule.DECORATIVE_EMOJI: re.compile(f"[{_EMOJI}]+"),
    # The quotative `と` is required, so ordinary passives such as
    # 「実装されている」 do not match; only agentless attribution does.
    ForbiddenRule.ABSENT_SUBJECT: re.compile(
        r"と(?:言われて|いわれて|されて|考えられて|見られて|みられて)(?:いる|いた|います)"
        r"|(?:注目|期待)されて(?:いる|います)"
        r"|(?:話題|議論)(?:を呼んで|になって)(?:いる|います)"
    ),
    ForbiddenRule.ABSTRACT_PRAISE: re.compile(
        r"画期的|革新的|圧倒的|驚異的|素晴らし|目覚ましい|注目に値する|他ならな|まさに|極めて重要"
    ),
    ForbiddenRule.HYPE: re.compile(
        r"必見|見逃せな|衝撃|激変|一変させ|驚くべき|革命的|ゲームチェンジャー|[Gg]ame[ -][Cc]hanger"
    ),
    # `だけでなく` is not here: it is the artifact often enough to notice, but it
    # is also how a factual enumeration is written (「CLI だけでなく SDK にも入った」),
    # and a false positive here would stop the day's publication. The two
    # remaining wordings are narrow enough to be the artifact on their own.
    ForbiddenRule.MECHANICAL_CONTRAST: re.compile(
        r"ではなく[、,].{0,30}?(?:である|だ)[。.]|単なる.{0,20}?ではな"
    ),
}


@dataclass(frozen=True)
class ForbiddenArtifact:
    """One match. `location` names the field the text came from, Pydantic-style."""

    rule: ForbiddenRule
    location: str
    start: int
    matched: str

    @property
    def description(self) -> str:
        return RULE_DESCRIPTIONS[self.rule]

    def __str__(self) -> str:
        where = self.location or "<text>"
        return f"{where}[{self.start}] {self.rule.value}: {self.description} ({self.matched!r})"


def check_forbidden_artifacts(text: str, *, location: str = "") -> tuple[ForbiddenArtifact, ...]:
    """Return every banned artifact in `text`, ordered by position then rule name.

    Pure: it reports and never rewrites. `render_digest` raises on a non-empty
    result; a caller that wants to inspect the text first calls this directly.
    """
    found = [
        ForbiddenArtifact(
            rule=rule,
            location=location,
            start=match.start(),
            matched=match.group()[:MATCHED_MAX_CHARS],
        )
        for rule, pattern in _PATTERNS.items()
        for match in pattern.finditer(text)
    ]
    found.sort(key=lambda artifact: (artifact.start, artifact.rule.value))
    return tuple(found)
