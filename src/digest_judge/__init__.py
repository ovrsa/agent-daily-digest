"""The Judge: an audit of the Selector's result in a separate prompt and context.

Import from this package, not from its submodules. `select_targets` picks the
articles to audit by fixed rules, `Judge.audit` makes one structured call
through `digest_observe.measured_call` and returns a `JudgeResult`, and
`render_report` turns it into the commit comment a person answers weekly. A
model failure is a `JudgeResult` with `error` set, so it never stops publishing.
"""

from .judge import (
    CALL_ID,
    DEFAULT_RETRY,
    EXCERPT_CHARS_PER_TARGET,
    RULE_HINTS,
    Invoker,
    Judge,
    JudgeResult,
    checked,
    retry_note,
)
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, audit_prompt
from .report import ANSWERS, TEXT_MAX_CHARS, finding_metrics, render_report
from .targets import AuditTarget, boundary_score, select_targets

__all__ = [
    "ANSWERS",
    "CALL_ID",
    "DEFAULT_RETRY",
    "EXCERPT_CHARS_PER_TARGET",
    "PROMPT_VERSION",
    "RULE_HINTS",
    "SYSTEM_PROMPT",
    "TEXT_MAX_CHARS",
    "AuditTarget",
    "Invoker",
    "Judge",
    "JudgeResult",
    "audit_prompt",
    "boundary_score",
    "checked",
    "finding_metrics",
    "render_report",
    "retry_note",
    "select_targets",
]
