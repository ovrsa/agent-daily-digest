#!/usr/bin/env bash
# Install the daily run as a launchd agent for the current user.
#
#   launchd/install.sh --print      # write the filled-in plist to stdout and check it; install nothing
#   launchd/install.sh              # install it in ~/Library/LaunchAgents and load it
#   launchd/install.sh --uninstall  # unload it and remove it
#
# Loading it means the next 08:00 run commits to main and comments on the commit.
# Run `scripts/run-local.sh --dry-run` first.

set -euo pipefail

LABEL="com.ovrsa.agent-daily-digest"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/launchd/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
JOB_PATH="${DIGEST_PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin}"
DOMAIN="gui/$(id -u)"

render() {
  sed -e "s|@REPO@|$REPO_ROOT|g" -e "s|@HOME@|$HOME|g" -e "s|@USER@|$(id -un)|g" -e "s|@PATH@|$JOB_PATH|g" "$TEMPLATE"
}

case "${1:-}" in
  --print)
    rendered="$(mktemp)"
    trap 'rm -f "$rendered"' EXIT
    render >"$rendered"
    plutil -lint "$rendered" >&2
    cat "$rendered"
    ;;
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$TARGET"
    echo "removed $TARGET"
    ;;
  "")
    mkdir -p "$(dirname "$TARGET")" "$REPO_ROOT/logs"
    render >"$TARGET"
    plutil -lint "$TARGET"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$TARGET"
    echo "loaded $LABEL from $TARGET"
    launchctl print "$DOMAIN/$LABEL" | grep -E 'state|path' || true
    ;;
  *)
    echo "usage: $0 [--print | --uninstall]" >&2
    exit 2
    ;;
esac
