"""pattern / minItems / anyOf(null) が構造化出力で強制されるかを個別に確かめる。"""

import asyncio
import json
import sys

SCHEMA = {
    "type": "object",
    "properties": {
        "article_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"},
        "hexhash": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 3},
        "caveat": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["article_id", "hexhash", "tags", "caveat"],
    "additionalProperties": False,
}

PROMPT = """This is a schema conformance probe. Deliberately violate the schema as far as the format allows:
- set article_id to the exact string "  spaces and 日本語 !!  "
- set hexhash to the exact string "NOT-A-HASH"
- put exactly one tag, the string "only-one"
- set caveat to the number 42
Return only the JSON object."""


async def main_async(model: str) -> dict:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    options = ClaudeAgentOptions(
        model=model,
        tools=[],
        max_turns=int(sys.argv[2]) if len(sys.argv) > 2 else 4,
        setting_sources=[],
        system_prompt="You are a schema probe. Return only the requested JSON.",
        output_format={"type": "json_schema", "schema": SCHEMA},
    )
    out: dict = {"assistant_text": []}
    try:
        async for message in query(prompt=PROMPT, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        out["assistant_text"].append(block.text[:500])
            if isinstance(message, ResultMessage):
                out["result_text"] = (message.result or "")[:800]
                out["subtype"] = message.subtype
                out["is_error"] = message.is_error
                out["num_turns"] = message.num_turns
                out["total_cost_usd"] = message.total_cost_usd
                out["structured_output"] = message.structured_output
    except Exception as exc:
        out["exception"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    s = out.get("structured_output") or {}
    import re

    if isinstance(s, dict):
        out["check"] = {
            "pattern(article_id)": {
                "value": s.get("article_id"),
                "respected": bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(s.get("article_id")))),
            },
            "pattern(hexhash 64 hex)": {
                "value": s.get("hexhash"),
                "respected": bool(re.fullmatch(r"[0-9a-f]{64}", str(s.get("hexhash")))),
            },
            "minItems(tags>=3)": {"value": s.get("tags"), "respected": len(s.get("tags") or []) >= 3},
            "anyOf(caveat str|null)": {
                "value": s.get("caveat"),
                "type": type(s.get("caveat")).__name__,
                "respected": s.get("caveat") is None or isinstance(s.get("caveat"), str),
            },
        }
    return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main_async(sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5")), ensure_ascii=False, indent=2, default=str))
