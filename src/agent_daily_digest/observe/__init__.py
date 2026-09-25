"""Run metrics: collection, aggregation and presentation, kept apart.

Import from this package, not from its submodules.

- collection: `RunRecorder` (run and stages), `measured_call` (every LLM call)
- pricing: `load_pricing`, `estimate_cost` from `config.json`
- retention: `MetricsStore` writes one JSON per run under `logs/metrics/`
- aggregation: `summarize` reduces a `RunMetrics` to a `RunSummary`
- presentation: `render_summary` turns a `RunSummary` into Markdown
- safety: `find_leaks` and `safe_detail` keep bodies, model I/O and secrets out

Nothing here imports the model SDK; `agent_daily_digest.llm` supplies the real invoker.
"""

from .recorder import Clock, utc_now
from .llm import (
    DEFAULT_MAX_ATTEMPTS,
    CallOutcome,
    CallSpec,
    Invoke,
    LLMInvocationError,
    LLMResponse,
    OutputRejected,
    Parse,
    RetryPolicy,
    invocation_error,
    measured_call,
)
from .pricing import (
    MODELS_KEY,
    PRICING_KEY,
    ModelPrice,
    ModelUsage,
    PricingTable,
    estimate_cost,
    load_pricing,
    token_usage,
)
from .recorder import (
    TOLERATED_STAGE_FAILURES,
    RunAborted,
    RunRecorder,
    Sink,
    StageFailed,
    article_metrics,
    classify_exception,
    derive_status,
    new_run_id,
)
from .report import render_summary
from .safety import (
    LEAK_MIN_CHARS,
    LEAK_WINDOW_CHARS,
    SECRET_PATTERNS,
    Leak,
    MetricsLeakError,
    describe_exception,
    find_leaks,
    redact_secrets,
    safe_detail,
)
from .store import DEFAULT_METRICS_DIR, MAX_RUN_FILES, RETENTION_DAYS, MetricsStore, metrics_filename
from .summary import RoleUsage, RunSummary, StageLine, summarize

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_METRICS_DIR",
    "LEAK_MIN_CHARS",
    "LEAK_WINDOW_CHARS",
    "MAX_RUN_FILES",
    "MODELS_KEY",
    "PRICING_KEY",
    "RETENTION_DAYS",
    "SECRET_PATTERNS",
    "TOLERATED_STAGE_FAILURES",
    "CallOutcome",
    "CallSpec",
    "Clock",
    "Invoke",
    "LLMInvocationError",
    "LLMResponse",
    "Leak",
    "MetricsLeakError",
    "MetricsStore",
    "ModelPrice",
    "ModelUsage",
    "OutputRejected",
    "Parse",
    "PricingTable",
    "RetryPolicy",
    "RoleUsage",
    "RunAborted",
    "RunRecorder",
    "RunSummary",
    "Sink",
    "StageFailed",
    "StageLine",
    "article_metrics",
    "classify_exception",
    "derive_status",
    "describe_exception",
    "estimate_cost",
    "find_leaks",
    "invocation_error",
    "load_pricing",
    "measured_call",
    "metrics_filename",
    "new_run_id",
    "redact_secrets",
    "render_summary",
    "safe_detail",
    "summarize",
    "token_usage",
    "utc_now",
]
