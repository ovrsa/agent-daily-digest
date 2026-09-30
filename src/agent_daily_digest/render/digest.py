"""Assemble one daily digest from the Selector's structured output.

The renderer is mechanical. It does not choose, rank, shorten or reword
anything: the wording comes from `DigestEntry`, the running order comes from
`SelectorOutput`, and the article metadata comes from `NormalizedArticle`. No
model is called here, which is what makes the same input produce the same bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum

from agent_daily_digest.contracts.articles import NormalizedArticle
from agent_daily_digest.contracts.editorial import (
    MUST_READ_MAX,
    WORTH_KNOWING_MAX,
    IncludedArticle,
    SelectorOutput,
    Tier,
)


class RenderError(Exception):
    """A rendering failure that prevents publication."""


class MissingArticleError(RenderError):
    """An included article has no normalized metadata."""

    def __init__(self, article_ids: tuple[str, ...]) -> None:
        self.article_ids = article_ids
        super().__init__("no article metadata for: " + ", ".join(article_ids))


class TierCapError(RenderError):
    """A tier exceeds the output contract's cap."""

    def __init__(self, tier: Tier, count: int, cap: int) -> None:
        self.tier = tier
        self.count = count
        self.cap = cap
        super().__init__(f"{tier.value} holds {count} entries, the cap is {cap}")


class ForbiddenArtifactError(RenderError):
    """Selector text contains an artifact that the output contract bans."""

    def __init__(self, findings: tuple[ForbiddenArtifact, ...]) -> None:
        self.findings = findings
        super().__init__(f"{len(findings)} forbidden artifact(s): " + "; ".join(str(finding) for finding in findings))


class IndexMarkerError(RenderError):
    """`digests/README.md` has missing or misordered index markers."""


DIGEST_TYPE = "agent-daily-digest"
DIGEST_TITLE = "Agent / Coding Agent Daily Digest"

_TIERS: tuple[tuple[Tier, str, int], ...] = (
    (Tier.MUST_READ, "Must Read", MUST_READ_MAX),
    (Tier.WORTH_KNOWING, "Worth Knowing", WORTH_KNOWING_MAX),
)

# Wording fixed by the Design Doc's Output contract.
_OVERVIEW = "今日の一覧"
_WHAT_HAPPENED = "何をしたか／何が分かったか"
_WHY_READ = "読む理由"
_EVIDENCE = "根拠"
_CAVEAT = "留保"


def digest_filename(digest_date: date) -> str:
    """`digests/` keeps one file per day, named by date so the index can sort it."""
    return f"{digest_date.isoformat()}.md"


def render_digest(
    selector_output: SelectorOutput,
    articles: Mapping[str, NormalizedArticle],
    digest_date: date,
) -> str | None:
    """Return the digest Markdown, or `None` when nothing was adopted.

    Adopting nothing is a normal outcome (Design Doc, Failure policy), so the
    caller writes no file rather than an empty digest.

    Raises `MissingArticleError` when an included article has no metadata,
    `TierCapError` when a tier exceeds its cap, and `ForbiddenArtifactError`
    when the Selector-authored text contains a banned artifact. Each is raised
    before any text is returned, so a rejected digest is never published.
    """
    if not selector_output.included:
        return None
    _check_caps(selector_output)
    _check_articles_present(selector_output, articles)
    _check_forbidden(selector_output)

    lines = [
        "---",
        f"date: {digest_date.isoformat()}",
        f"type: {DIGEST_TYPE}",
        "---",
        "",
        f"# {DIGEST_TITLE} {digest_date.isoformat()}",
        "",
        f"## {_OVERVIEW}",
        "",
    ]
    numbered = list(enumerate(_running_order(selector_output), start=1))
    for number, (heading, included) in numbered:
        lines += _overview_lines(number, heading, included, articles[included.article_id])
    current = None
    for number, (heading, included) in numbered:
        if heading != current:
            lines += ["", f"## {heading}"]
            current = heading
        lines += _entry_lines(number, included, articles[included.article_id])
    return "\n".join(lines) + "\n"


def forbidden_artifacts_in(selector_output: SelectorOutput) -> tuple[ForbiddenArtifact, ...]:
    """Every banned artifact the digest text would carry, without rendering it."""
    return tuple(
        artifact
        for location, text in _checked_spans(selector_output)
        for artifact in check_forbidden_artifacts(text, location=location)
    )


def _bucket(selector_output: SelectorOutput, tier: Tier) -> tuple[IncludedArticle, ...]:
    return selector_output.must_read if tier is Tier.MUST_READ else selector_output.worth_knowing


def _running_order(selector_output: SelectorOutput) -> list[tuple[str, IncludedArticle]]:
    """Every adopted article with its tier heading, numbered once for the overview and the body."""
    return [(heading, included) for tier, heading, _cap in _TIERS for included in _bucket(selector_output, tier)]


def _overview_lines(number: int, heading: str, included: IncludedArticle, article: NormalizedArticle) -> list[str]:
    # The two trailing spaces are a Markdown hard break, so the headline sits on
    # its own line under the title. The indent keeps it inside the list item.
    marker = f"{number}. "
    return [
        f"{marker}**{heading}** {link(article.title, article.canonical_url)}  ",
        f"{' ' * len(marker)}{inline(included.entry.headline)}",
    ]


def _entry_lines(number: int, included: IncludedArticle, article: NormalizedArticle) -> list[str]:
    entry = included.entry
    lines = [
        "",
        f"### {number}. {link(article.title, article.canonical_url)}",
        "",
        _meta_line(article),
    ]
    parts = [
        (_WHAT_HAPPENED, entry.what_happened.text),
        (_WHY_READ, entry.why_read),
        (_EVIDENCE, entry.evidence.text),
    ]
    if entry.caveat is not None:
        parts.append((_CAVEAT, entry.caveat.text))
    for label, text in parts:
        lines += ["", f"**{label}**", "", inline(text)]
    return lines


def _meta_line(article: NormalizedArticle) -> str:
    # `published_at` is rendered in the offset it carries. Converting to one
    # zone would be an editorial rule the contract does not state.
    parts = [f"ソース: {article.source_id}"]
    if article.author is not None:
        parts.append(f"著者: {inline(article.author)}")
    parts.append(f"公開: {article.published_at.date().isoformat()}")
    return " / ".join(parts)


def _check_caps(selector_output: SelectorOutput) -> None:
    for tier, _heading, cap in _TIERS:
        count = len(_bucket(selector_output, tier))
        if count > cap:
            raise TierCapError(tier, count, cap)


def _check_articles_present(selector_output: SelectorOutput, articles: Mapping[str, NormalizedArticle]) -> None:
    missing = tuple(a.article_id for a in selector_output.included if a.article_id not in articles)
    if missing:
        raise MissingArticleError(missing)


def _check_forbidden(selector_output: SelectorOutput) -> None:
    findings = forbidden_artifacts_in(selector_output)
    if findings:
        raise ForbiddenArtifactError(findings)


def _checked_spans(selector_output: SelectorOutput) -> Iterator[tuple[str, str]]:
    """The Selector-authored text, paired with a location a caller can act on.

    Titles and URLs are excluded on purpose: they are quoted source text, and
    an em dash in an original English title is the publisher's, not ours.
    """
    for tier, _heading, _cap in _TIERS:
        for index, included in enumerate(_bucket(selector_output, tier)):
            entry = included.entry
            at = f"{tier.value}[{index}].entry"
            yield f"{at}.what_happened.text", entry.what_happened.text
            yield f"{at}.why_read", entry.why_read
            yield f"{at}.evidence.text", entry.evidence.text
            if entry.caveat is not None:
                yield f"{at}.caveat.text", entry.caveat.text
            yield f"{at}.headline", entry.headline


_LINK_TEXT_ESCAPES = str.maketrans({"\\": "\\\\", "[": "\\[", "]": "\\]"})
_DESTINATION_ESCAPES = str.maketrans({"<": "%3C", ">": "%3E"})
_DESTINATION_NEEDS_BRACKETS = "()<> "


def inline(text: str) -> str:
    """Collapse whitespace so source text cannot alter the Markdown structure."""
    return " ".join(text.split())


def link(text: str, url: str) -> str:
    """Render an inline link while preserving legal URL bytes."""
    return f"[{inline(text).translate(_LINK_TEXT_ESCAPES)}]({_destination(url)})"


def _destination(url: str) -> str:
    if any(ch in url for ch in _DESTINATION_NEEDS_BRACKETS):
        return f"<{url.translate(_DESTINATION_ESCAPES)}>"
    return url


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
    ForbiddenRule.MECHANICAL_CONTRAST: re.compile(r"ではなく[、,].{0,30}?(?:である|だ)[。.]|単なる.{0,20}?ではな"),
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
