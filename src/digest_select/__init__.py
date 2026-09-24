"""The Selector: six-axis evaluation, decision and entry text from Evidence Packets.

Import from this package, not from its submodules. `Selector.select` makes one
structured call through `digest_observe.measured_call` and returns a
`SelectionResult` carrying the `SelectorOutput` with its model and prompt
version. `selection_issues` is the check a decision has to pass, usable on its own.
"""

from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, selection_prompt
from .selector import (
    CALL_ID,
    DEFAULT_RETRY,
    EXCERPT_CHARS_PER_PACKET,
    Invoker,
    SelectionResult,
    Selector,
    checked,
    selection_issues,
)

__all__ = [
    "CALL_ID",
    "DEFAULT_RETRY",
    "EXCERPT_CHARS_PER_PACKET",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "Invoker",
    "SelectionResult",
    "Selector",
    "checked",
    "selection_issues",
    "selection_prompt",
]
