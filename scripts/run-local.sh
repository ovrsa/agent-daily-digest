#!/usr/bin/env bash
# Local manual entrypoint for agent-daily-digest.
# Fetches all sources via src/fetch.py, then invokes `claude -p` with the
# editorial system prompt to produce the digest Markdown under digests/.
#
# Run manually:
#   ./scripts/run-local.sh
#
# For scheduled CLOUD execution, this script is NOT used — see
# routine/prompt.md and the claude.ai remote routine instead.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_ROOT/config/config.json"
SYSTEM_PROMPT="$REPO_ROOT/prompts/system-prompt.md"
FETCH="$REPO_ROOT/src/fetch.py"
LOG_DIR="$REPO_ROOT/logs"
TMP_DIR="${TMPDIR:-/tmp}"

mkdir -p "$LOG_DIR"

DATE="$(date +%Y-%m-%d)"
RAW_FILE="$TMP_DIR/llm-digest-raw-$DATE.json"
LOG_FILE="$LOG_DIR/run-$DATE.log"

# Resolve config values via python (stdlib only)
DIGEST_DIR_REL="$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('digest_dir','digests'))")"
MODEL="$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('summary_model','claude-sonnet-4-6'))")"

# digest_dir is relative to repo root unless absolute
case "$DIGEST_DIR_REL" in
  /*) OUTPUT_DIR="$DIGEST_DIR_REL" ;;
  *)  OUTPUT_DIR="$REPO_ROOT/$DIGEST_DIR_REL" ;;
esac

mkdir -p "$OUTPUT_DIR"
OUT_FILE="$OUTPUT_DIR/$DATE.md"

{
  echo "==== agent-daily-digest run: $(date -Iseconds) ===="
  echo "[run] config:        $CONFIG"
  echo "[run] output:        $OUT_FILE"
  echo "[run] raw:           $RAW_FILE"
  echo "[run] model:         $MODEL"
} | tee -a "$LOG_FILE"

# Step 1: fetch
echo "[run] fetching sources..." | tee -a "$LOG_FILE"
python3 "$FETCH" --config "$CONFIG" --out "$RAW_FILE" 2>>"$LOG_FILE"

if [[ ! -s "$RAW_FILE" ]]; then
  echo "[run] ERROR: fetch produced empty output" | tee -a "$LOG_FILE" >&2
  exit 2
fi

# Step 2: summarize via claude -p (uses logged-in Claude Code session auth)
echo "[run] invoking claude -p..." | tee -a "$LOG_FILE"

PROMPT="今日の LLM/Coding Agent ダイジェストを生成してください。

入力 (RAW_FILE): $RAW_FILE
出力 (OUT_FILE): $OUT_FILE

system-prompt に従い、Read で入力を読み、Write で出力ファイルを生成して終了してください。"

# --permission-mode acceptEdits: auto-accept Read/Write within --allowedTools
# --allowedTools: minimum surface for this job
# --append-system-prompt-file: prepends editorial role definition
# --add-dir: grant access to input JSON dir and output dir
# (no --bare: bare requires ANTHROPIC_API_KEY; we rely on the logged-in
#  Claude Code session auth via keychain/OAuth)
claude -p \
  --model "$MODEL" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Write" \
  --append-system-prompt-file "$SYSTEM_PROMPT" \
  --add-dir "$TMP_DIR" \
  --add-dir "$OUTPUT_DIR" \
  --no-session-persistence \
  --output-format text \
  "$PROMPT" \
  2>>"$LOG_FILE" | tee -a "$LOG_FILE" >/dev/null

if [[ ! -s "$OUT_FILE" ]]; then
  echo "[run] ERROR: claude did not produce $OUT_FILE" | tee -a "$LOG_FILE" >&2
  exit 3
fi

echo "[run] SUCCESS: $OUT_FILE ($(wc -l <"$OUT_FILE") lines)" | tee -a "$LOG_FILE"

# Step 3: cleanup raw file (keep logs for debugging)
rm -f "$RAW_FILE"
