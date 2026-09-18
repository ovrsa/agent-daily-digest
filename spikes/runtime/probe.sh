#!/usr/bin/env bash
# Issue #2 spike: scheduled job (remote routine) の実行環境を1回の実行で観測する。
# 値を出さない: 環境変数は sdk_probe.py が名前と状態だけを出す。git remote URL は認証部分を伏せる。
set -u
W="${PROBE_WORKDIR:-/tmp/spike2}"
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SID="${CLAUDE_CODE_REMOTE_SESSION_ID:-none}"
mkdir -p "$W"
section() { printf '\n### %s (%s)\n' "$1" "$(date -u +%H:%M:%SZ)"; }

section "5-runtime"
uname -srm
grep -E '^(NAME|VERSION)=' /etc/os-release
for c in python3 uv pip pipx gh git claude node jq; do
  printf '%s: ' "$c"
  if command -v "$c" >/dev/null; then "$c" --version 2>&1 | head -1; else echo missing; fi
done
echo "nproc=$(nproc 2>/dev/null)"; free -g 2>/dev/null | sed -n 2p; df -h / 2>/dev/null | tail -1

section "3-session-git"
echo "repo=$REPO"
echo "branch=$(git -C "$REPO" branch --show-current)"
echo "head=$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" remote get-url origin | sed -E 's#//[^@/]*@#//<redacted>@#'
echo "session_id_present=$([ "$SID" = none ] && echo no || echo yes)"

section "5-deps"
start=$(date +%s)
PKGS="claude-agent-sdk pydantic pytest"
if python3 -m venv "$W/venv" > "$W/pip.log" 2>&1 && "$W/venv/bin/pip" install -q $PKGS >> "$W/pip.log" 2>&1; then
  echo "deps=venv+pip ok seconds=$(( $(date +%s) - start ))"
else
  echo "deps=venv+pip failed seconds=$(( $(date +%s) - start )); reason:"
  tail -5 "$W/pip.log"
  echo "fallback uv"
  rm -rf "$W/venv"; start=$(date +%s)
  uv venv -q "$W/venv" > "$W/uv.log" 2>&1 && uv pip install -q --python "$W/venv/bin/python" $PKGS >> "$W/uv.log" 2>&1
  echo "deps=uv exit=$? seconds=$(( $(date +%s) - start ))"
  tail -3 "$W/uv.log"
fi
"$W/venv/bin/python" --version
"$W/venv/bin/python" -c 'import importlib.metadata as m
for p in ["claude-agent-sdk","pydantic","pytest","mcp"]:
    try: print(p, m.version(p))
    except Exception as e: print(p, "absent")'
ls "$W"/venv/lib/python3*/site-packages/claude_agent_sdk/_bundled/ 2>&1 | head -3

section "5-outbound"
for url in \
  https://claude.com/blog https://www.anthropic.com/engineering https://boristane.com/blog \
  https://nyosegawa.com/ https://simonwillison.net/2026/ https://addyosmani.com/blog/ \
  "https://hn.algolia.com/api/v1/search?query=agent" https://news.ycombinator.com/ \
  https://www.reddit.com/r/ClaudeAI/.rss https://huggingface.co/api/daily_papers \
  "https://export.arxiv.org/api/query?search_query=all:agent" https://www.latent.space/feed \
  https://www.interconnects.ai/feed https://news.smol.ai/ \
  "https://api.github.com/repos/anthropics/claude-code/releases?per_page=1" \
  https://pypi.org/simple/claude-agent-sdk/ https://api.anthropic.com/ ; do
  code=$(curl -s -o /dev/null -D "$W/h" -m 25 -w '%{http_code} %{time_total}s' "$url" 2>&1)
  deny=$(grep -i '^x-deny-reason' "$W/h" 2>/dev/null | tr -d '\r')
  echo "$url -> $code $deny"
done

section "1-2-sdk-bundled-maxturns4"
timeout 420 "$W/venv/bin/python" "$W/sdk_probe.py" claude-sonnet-5 4; echo "exit=$?"
section "1-2-sdk-session-cli-maxturns1"
timeout 420 "$W/venv/bin/python" "$W/sdk_probe.py" claude-sonnet-5 1 "$(command -v claude || echo '')"; echo "exit=$?"

section "3-schema-and-errors"
PYTHONPATH="$REPO/src" timeout 600 "$W/venv/bin/python" "$W/schema_probe.py" claude-sonnet-5 > "$W/schema.json" 2>"$W/schema.err"
echo "schema_probe_exit=$?"
tail -3 "$W/schema.err"
python3 - "$W/schema.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print("schema.json unreadable:", e); raise SystemExit(0)
print(json.dumps({
  "schema_inventory": d.get("schema_inventory"),
  "constraint_check": d.get("constraint_check"),
  "adversarial_result": (d.get("adversarial") or {}).get("result"),
  "adversarial_init": (d.get("adversarial") or {}).get("init"),
  "adversarial_exception": (d.get("adversarial") or {}).get("exception"),
  "failure_call": d.get("failure_call"),
  "exceptions": d.get("exceptions"),
  "contract_error_kinds": d.get("contract_error_kinds"),
  "token_usage_fields": d.get("token_usage_fields"),
}, ensure_ascii=False, indent=2, default=str))
PY

section "4-persistence"
echo "home=$HOME"
ls -la /tmp 2>/dev/null | head -6
echo "setup_script_marker=$(ls -d /home/*/.claude 2>/dev/null | head -1)"
df -h /tmp 2>/dev/null | tail -1
echo "probe_done=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
