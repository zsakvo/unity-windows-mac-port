#!/bin/bash
# ---------------------------------------------------------------------------
# verify_port.sh - launch an assembled macOS port and check the player log for
# every failure signature this workflow knows about.
#
# Usage: verify_port.sh "/path/to/App.app" [--seconds 40] [--screenshot out.png]
#
# Checks, in the order they appear in a healthy log:
#   * Metal device chosen (not NullGfxDevice)
#   * no "was not built from editor" (that means the -force-gfx workaround is on,
#     and with it Unity disables shader loading entirely)
#   * no "Desired shader compiler platform 14 is not available in shader blob"
#     and no "ERROR: Shader ... is not supported on this GPU"
#   * no "The resource Internal-ErrorShader.shader could not be loaded"
#   * no DllNotFoundException (a Windows plugin without a macOS shim)
#   * no "Invalid serialized file version" (player does not match the data)
#   * process still alive at the end (early exits usually mean Application.Quit
#     from a missing-Steam / missing-plugin check)
# ---------------------------------------------------------------------------
set -uo pipefail

APP="${1:-}"; SECONDS_TO_WATCH=40; SHOT=""
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --seconds) SECONDS_TO_WATCH="$2"; shift 2 ;;
    --screenshot) SHOT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -d "$APP" ] || { echo "usage: verify_port.sh /path/to/App.app" >&2; exit 2; }
APP="$(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"

DATA="$APP/Contents/Resources/Data"
[ -d "$DATA" ] || { echo "no Data directory inside $APP" >&2; exit 1; }
COMPANY="$(sed -n 1p "$DATA/app.info" 2>/dev/null | tr -d '\r')"
PRODUCT="$(sed -n 2p "$DATA/app.info" 2>/dev/null | tr -d '\r')"
LOG="$HOME/Library/Logs/${COMPANY:-Unity}/${PRODUCT:-Player}/Player.log"

case "$APP" in
  "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
    echo "!! $APP lives in a TCC-protected folder."
    echo "   macOS blocks such apps from reading their own bundle: the game will hang"
    echo "   in open() (or exit).  Move it to ~/Applications or /Applications first."
    echo ;;
esac

echo "==> launching $APP"
echo "    player log: $LOG"
BEFORE_LINES=$(wc -l < "$LOG" 2>/dev/null || echo 0)
rm -f "$LOG" 2>/dev/null || true
open -n "$APP" || { echo "launch failed" >&2; exit 1; }

alive_at_end="no"
for i in $(seq 1 "$SECONDS_TO_WATCH"); do
  sleep 1
  if ! pgrep -f "$APP/Contents/MacOS" >/dev/null 2>&1; then
    alive_at_end="no (exited after ${i}s)"
    break
  fi
  alive_at_end="yes (still running after ${SECONDS_TO_WATCH}s)"
done

sleep 1
PIDS=$(pgrep -f "$APP/Contents/MacOS" 2>/dev/null || true)
for p in $PIDS; do kill "$p" 2>/dev/null; done

echo
echo "=== findings ==="
report() { printf '  %-58s %s\n' "$1" "$2"; }
grepq() { grep -c "$1" "$LOG" 2>/dev/null || true; }

if [ ! -f "$LOG" ]; then
  report "player log" "MISSING - the app never started (check codesign / quarantine)"
  exit 1
fi

report "Metal device initialised" \
  "$([ "$(grepq 'Using device')" -gt 0 ] && echo yes || echo 'NO - see NullGfxDevice below')"
report "NullGfxDevice (graphics API stamp not patched)" \
  "$(grepq 'NullGfxDevice') occurrence(s)"
report '"was not built from editor" (force-gfx workaround in use)' \
  "$(grepq 'was not built from editor') occurrence(s)"
report '"shader compiler platform 14 not available"' \
  "$(grepq 'compiler platform 14 is not available') occurrence(s)"
report '"ERROR: Shader ... not supported on this GPU"' \
  "$(grepq 'ERROR: Shader') occurrence(s)"
report '"Internal-ErrorShader could not be loaded"' \
  "$(grepq 'Internal-ErrorShader') occurrence(s)"
report 'DllNotFoundException (missing plugin shim)' \
  "$(grepq 'DllNotFoundException') occurrence(s)"
report '"Invalid serialized file version"' \
  "$(grepq 'Invalid serialized file version') occurrence(s)"
report 'native crash (SIGSEGV / mono backtrace)' \
  "$(grepq 'Got a SIGSEGV') occurrence(s)"
report "process" "$alive_at_end"

if [ "$(grepq 'ERROR: Shader')" -gt 0 ]; then
  echo
  echo "  shaders still without Metal sub-programs:"
  grep -oE "ERROR: Shader [^\"]*" "$LOG" | sort -u | sed 's/^/    - /'
fi
if [ "$(grepq 'DllNotFoundException')" -gt 0 ]; then
  echo
  echo "  missing native libraries:"
  grep -oE "DllNotFoundException: [^ ]*" "$LOG" | sort -u | sed 's/^/    - /'
  echo "    (generate shims with gen_plugin_stubs.py --managed <Data/Managed>)"
fi

if [ -n "$SHOT" ]; then
  screencapture -x "$SHOT" 2>/dev/null && echo && echo "  screenshot: $SHOT" || \
    echo "  (screencapture failed - needs Screen Recording permission)"
fi
echo
echo "full log: $LOG"
