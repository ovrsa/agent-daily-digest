#!/usr/bin/env bash
# Run the daily digest from this checkout: `python -m agent_daily_digest`, logged under logs/.
#
#   ./ops/run-local.sh                              # real run: commit and push to main, comment on the commit
#   ./ops/run-local.sh --dry-run                    # publish nothing; the result is in logs/dry-run/<run_id>/
#   ./ops/run-local.sh --dry-run --max-articles 5   # the same, researching at most 5 articles
#
# launchd runs this script (see ops/launchd/install.sh). Every argument goes to the pipeline.
# The exit code is the pipeline's: 0 when the digest run finished (a failed Judge or
# comment still counts, the digest is out), 1 when it did not.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${DIGEST_PYTHON:-$REPO_ROOT/.venv/bin/python}"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/run-$(date +%Y-%m-%d).log"
LOG_RETENTION_DAYS=35 # as long as logs/metrics/, so a weekly review always has both

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -maxdepth 1 -name 'run-*.log' -mtime "+$LOG_RETENTION_DAYS" -delete

if [[ ! -x "$PYTHON" ]]; then
  echo "[run] $PYTHON not found. Set it up with: python3 -m venv .venv && .venv/bin/pip install -e ." | tee -a "$LOG_FILE" >&2
  exit 2
fi

cd "$REPO_ROOT"
{
  echo "==== agent-daily-digest $(date '+%Y-%m-%dT%H:%M:%S%z') args: $* ===="
  "$PYTHON" -m agent_daily_digest --repo "$REPO_ROOT" "$@"
} 2>&1 | tee -a "$LOG_FILE"
