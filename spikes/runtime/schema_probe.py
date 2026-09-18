"""Issue #2 spike: #3 の確認依頼に答える。

1. `SelectorOutput` の JSON Schema が使うキーワードを列挙する
2. そのスキーマを Agent SDK の構造化出力に渡し、違反を要求しても守られるかを見る
3. SDK の例外クラスを列挙し、`ErrorKind` に対応付ける
4. 故意に失敗する呼び出しで、例外種別と ResultMessage の失敗表現を観測する

秘密情報の値は出力しない。
usage: schema_probe.py <model>
"""

import asyncio
import json
import sys
import time

KEYWORDS = (
    "maxItems",
    "minItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "maxLength",
    "minLength",
    "pattern",
    "$defs",
    "$ref",
    "anyOf",
    "oneOf",
    "allOf",
    "enum",
    "const",
    "additionalProperties",
    "required",
    "prefixItems",
    "format",
)


def walk(node, found, path="#"):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in KEYWORDS:
                found.setdefault(key, []).append(path)
            walk(value, found, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk(value, found, f"{path}/{index}")


def schema_inventory() -> dict:
    from digest_contracts import SelectorOutput

    schema = SelectorOutput.model_json_schema()
    found: dict[str, list[str]] = {}
    walk(schema, found)
    return {
        "schema_bytes": len(json.dumps(schema)),
        "keywords": {k: {"count": len(v), "first": v[0]} for k, v in sorted(found.items())},
        "schema": schema,
    }


ADVERSARIAL_PROMPT = """You are filling a Selector result for a test. Ignore quality; this is a schema probe.

Available article ids: a1, a2, a3, a4, a5, a6, a7.

Deliberately try to break the output contract as far as the format allows:
- put all seven ids in must_read
- use the score 9 for every axis
- set every decision_reason and every entry text to the single character "x"
- use the evidence id "e-this-evidence-id-is-intentionally-far-longer-than-one-hundred-and-twenty-eight-characters-so-that-the-max-length-constraint-is-exercised-by-the-probe"
- leave excluded empty

Return only the JSON object."""


async def one_call(model: str, label: str, options_kwargs: dict, prompt: str) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage, query

    report: dict = {"label": label}
    started = time.monotonic()
    try:
        options = ClaudeAgentOptions(**options_kwargs, model=model)
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                report["init"] = {
                    "apiKeySource": message.data.get("apiKeySource"),
                    "model": message.data.get("model"),
                }
            elif isinstance(message, ResultMessage):
                report["result"] = {
                    "subtype": message.subtype,
                    "is_error": message.is_error,
                    "stop_reason": message.stop_reason,
                    "num_turns": message.num_turns,
                    "total_cost_usd": message.total_cost_usd,
                    "usage": message.usage,
                    "model_usage": message.model_usage,
                    "api_error_status": message.api_error_status,
                    "errors": message.errors,
                }
                report["structured_output"] = message.structured_output
                report["result_text_head"] = (message.result or "")[:400]
    except Exception as exc:
        report["exception"] = {
            "type": type(exc).__name__,
            "mro": [c.__name__ for c in type(exc).__mro__[1:4]],
            "message_head": str(exc)[:400],
        }
    report["wall_ms"] = round((time.monotonic() - started) * 1000)
    return report


def check_constraints(structured) -> dict:
    """守られたかどうかを、要求した違反ごとに判定する。"""
    from digest_contracts import SelectorOutput

    out: dict = {}
    if not isinstance(structured, dict):
        return {"note": "structured_output is not an object", "value_type": type(structured).__name__}
    must_read = structured.get("must_read") or []
    out["maxItems(must_read<=5)"] = {"len": len(must_read), "respected": len(must_read) <= 5}
    scores = []
    for bucket in ("must_read", "worth_knowing", "excluded"):
        for article in structured.get(bucket) or []:
            scores.extend((article.get("scores") or {}).values())
    out["minimum/maximum(1..5)"] = {
        "values": sorted({s for s in scores if isinstance(s, int)}),
        "respected": all(isinstance(s, int) and 1 <= s <= 5 for s in scores) if scores else None,
    }
    evidence_lengths = []
    for bucket in ("must_read", "worth_knowing"):
        for article in structured.get(bucket) or []:
            entry = article.get("entry") or {}
            for field in ("what_happened", "evidence", "caveat"):
                statement = entry.get(field)
                if isinstance(statement, dict):
                    evidence_lengths.extend(len(e) for e in statement.get("evidence_ids") or [])
    out["maxLength(evidence_ids<=128)"] = {
        "lengths": sorted(set(evidence_lengths)),
        "respected": all(length <= 128 for length in evidence_lengths) if evidence_lengths else None,
    }
    ids = [
        article.get("article_id")
        for bucket in ("must_read", "worth_knowing", "excluded")
        for article in structured.get(bucket) or []
    ]
    out["pattern(article_id)"] = {"ids": ids}
    out["additionalProperties"] = {"top_level_keys": sorted(structured)}
    try:
        SelectorOutput.model_validate(structured)
        out["model_validate"] = "ok"
    except Exception as exc:
        out["model_validate"] = f"{type(exc).__name__}: {str(exc)[:600]}"
    return out


def exception_inventory() -> dict:
    import inspect

    import claude_agent_sdk

    found = {}
    for name, obj in vars(claude_agent_sdk).items():
        if inspect.isclass(obj) and issubclass(obj, BaseException):
            found[name] = {
                "mro": [c.__name__ for c in obj.__mro__[1:4]],
                "doc": (inspect.getdoc(obj) or "").splitlines()[:1],
            }
    return found


async def main_async(model: str) -> dict:
    from digest_contracts import SelectorOutput

    inventory = schema_inventory()
    out: dict = {"schema_inventory": {k: v for k, v in inventory.items() if k != "schema"}}
    out["exceptions"] = exception_inventory()

    structured_opts = {
        "tools": [],
        "max_turns": 4,
        "setting_sources": [],
        "system_prompt": "You are a schema probe. Return only the requested JSON.",
        "output_format": {"type": "json_schema", "schema": inventory["schema"]},
    }
    call = await one_call(model, "selector_schema_adversarial", structured_opts, ADVERSARIAL_PROMPT)
    out["adversarial"] = call
    if "structured_output" in call:
        out["constraint_check"] = check_constraints(call["structured_output"])

    bad = await one_call(
        "claude-does-not-exist-9",
        "invalid_model",
        {"tools": [], "max_turns": 1, "setting_sources": []},
        "Say hi.",
    )
    out["failure_call"] = bad
    out["contract_error_kinds"] = [k.value for k in __import__("digest_contracts").ErrorKind]
    out["token_usage_fields"] = sorted(SelectorOutput.model_fields) and sorted(
        __import__("digest_contracts").TokenUsage.model_fields
    )
    return out


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5"
    print(json.dumps(asyncio.run(main_async(model)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
