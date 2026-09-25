"""Issue #2 spike: Claude Agent SDK の実行・認証・メトリクスを観測する最小プローブ。

秘密情報の値は出力しない。環境変数は名前と状態（unset / placeholder / set）だけを出す。
usage: sdk_probe.py <model> <max_turns> [cli_path]
"""

import asyncio
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import time

AUTH_ENV = re.compile(
    r"^(ANTHROPIC_|CLAUDE_|CCR_|GH_TOKEN$|GITHUB_TOKEN$|HTTPS?_PROXY$|https?_proxy$|NO_PROXY$"
    r"|SSL_CERT_FILE$|REQUESTS_CA_BUNDLE$|NODE_EXTRA_CA_CERTS$)"
)


def env_state(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return "unset"
    if value == "proxy-injected":
        return "placeholder(proxy-injected)"
    if value == "":
        return "empty"
    return "set"


def runtime_report() -> dict:
    versions = {}
    for package in ("claude-agent-sdk", "pydantic", "pytest", "mcp"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "absent"
    return {
        "python": sys.version.split()[0],
        "python_ge_3_10": sys.version_info >= (3, 10),
        "platform": platform.platform(),
        "versions": versions,
        "claude_on_path": shutil.which("claude"),
        "auth_env_names": {
            name: env_state(name) for name in sorted(os.environ) if AUTH_ENV.match(name)
        },
    }


async def sdk_probe(model: str, max_turns: int, cli_path: str | None) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage, query

    options = ClaudeAgentOptions(
        cli_path=cli_path,
        model=model,
        tools=[],
        max_turns=max_turns,
        setting_sources=[],
        system_prompt="You are a probe. Answer with the requested JSON only.",
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    )
    report: dict = {"model_requested": model, "max_turns": max_turns, "cli": cli_path or "bundled"}
    started = time.monotonic()
    try:
        async for message in query(prompt='Return {"ok": true}.', options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                data = message.data
                report["init"] = {
                    "apiKeySource": data.get("apiKeySource"),
                    "model": data.get("model"),
                    "claude_code_version": data.get("claude_code_version"),
                }
            elif isinstance(message, ResultMessage):
                report["result"] = {
                    "subtype": message.subtype,
                    "is_error": message.is_error,
                    "stop_reason": message.stop_reason,
                    "duration_ms": message.duration_ms,
                    "duration_api_ms": message.duration_api_ms,
                    "num_turns": message.num_turns,
                    "total_cost_usd": message.total_cost_usd,
                    "usage": message.usage,
                    "model_usage": message.model_usage,
                    "structured_output": message.structured_output,
                    "api_error_status": message.api_error_status,
                    "errors": message.errors,
                }
    except Exception as exc:  # 観測が目的なので例外の型と先頭だけ残す
        report["exception"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    report["wall_ms"] = round((time.monotonic() - started) * 1000)
    return report


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5"
    max_turns = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    cli_path = sys.argv[3] if len(sys.argv) > 3 else None
    out = {"runtime": runtime_report(), "sdk": asyncio.run(sdk_probe(model, max_turns, cli_path))}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
