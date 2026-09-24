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

# Each value is set with plutil, which writes it as plist text: a path holding `&`, `<`
# or `|` comes through as it is, where a sed substitution would corrupt it.
render() {
  local out="$1"
  cp "$TEMPLATE" "$out"
  # An array index given to -replace inserts rather than replaces, so the array is rebuilt.
  plutil -replace ProgramArguments -array "$out"
  plutil -insert ProgramArguments -string /bin/bash -append "$out"
  plutil -insert ProgramArguments -string "$REPO_ROOT/scripts/run-local.sh" -append "$out"
  plutil -replace WorkingDirectory -string "$REPO_ROOT" "$out"
  plutil -replace EnvironmentVariables.PATH -string "$JOB_PATH" "$out"
  plutil -replace EnvironmentVariables.HOME -string "$HOME" "$out"
  plutil -replace EnvironmentVariables.USER -string "$(id -un)" "$out"
  plutil -replace StandardErrorPath -string "$REPO_ROOT/logs/launchd.log" "$out"
  if grep -q '@[A-Z]*@' "$out"; then
    echo "a placeholder in $TEMPLATE was left unfilled" >&2
    return 1
  fi
  plutil -lint "$out" >&2
}

case "${1:-}" in
  --print)
    rendered="$(mktemp)"
    trap 'rm -f "$rendered"' EXIT
    render "$rendered"
    cat "$rendered"
    ;;
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$TARGET"
    echo "removed $TARGET"
    ;;
  "")
    mkdir -p "$(dirname "$TARGET")" "$REPO_ROOT/logs"
    render "$TARGET"
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
