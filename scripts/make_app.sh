#!/bin/bash
# ---------------------------------------------------------------------------
# make_app.sh - assemble a native macOS .app for a Mono Unity game from the
# Windows build's player data plus Unity's own macOS player template.
#
#   Unity's "Mac Build Support (Mono)" module ships ready-made player bundles
#   under  Variations/macos_{arm64,x64,x64arm64}_{development,nondevelopment}_mono/
#   containing UnityPlayer.app (launcher + UnityPlayer.dylib + mono dylibs).
#   This script grafts the game's Data directory onto that skeleton.
#
# Usage:
#   make_app.sh --game "/path/GameDir" --player "/path/to/Variations" \
#               [--app-name "My Game"] [--bundle-id com.acme.game] [--out DIR] \
#               [--steam-appid 1234560] [--arch arm64|x64arm64]
#
# After this, run:  patch_game_data.py --data "<app>/Contents/Resources/Data" \
#                                     --donor <macOS-built donor app>
# ---------------------------------------------------------------------------
set -euo pipefail

GAME=""; PLAYER=""; APP_NAME=""; BUNDLE_ID=""; OUT_DIR=""; STEAM_APPID=""; ARCH="x64arm64"
while [ $# -gt 0 ]; do
  case "$1" in
    --game) GAME="$2"; shift 2 ;;
    --player) PLAYER="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --bundle-id) BUNDLE_ID="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --steam-appid) STEAM_APPID="$2"; shift 2 ;;
    --arch) ARCH="$2"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$GAME" ] && [ -n "$PLAYER" ] || { echo "need --game and --player" >&2; exit 2; }
GAME="$(cd "$GAME" && pwd)"
[ -d "$GAME" ] || { echo "no such game dir: $GAME" >&2; exit 1; }

# --- locate the player template -------------------------------------------------
if [ -d "$PLAYER/UnityPlayer.app" ]; then
  VARIATION="$PLAYER"                                  # pointed straight at a variation
  VARIATIONS_DIR="$(dirname "$PLAYER")"
elif [ -d "$PLAYER/macos_${ARCH}_nondevelopment_mono/UnityPlayer.app" ]; then
  VARIATIONS_DIR="$PLAYER"
  VARIATION="$PLAYER/macos_${ARCH}_nondevelopment_mono"
elif [ -d "$PLAYER/Variations" ]; then
  VARIATIONS_DIR="$PLAYER/Variations"
  VARIATION="$VARIATIONS_DIR/macos_${ARCH}_nondevelopment_mono"
else
  echo "cannot find a macos_${ARCH}_nondevelopment_mono/UnityPlayer.app under $PLAYER" >&2
  exit 1
fi
SUPPORT_DIR="$(dirname "$VARIATIONS_DIR")"             # .../MacStandaloneSupport

# --- game metadata -------------------------------------------------------------
EXE="$(cd "$GAME" && ls *.exe 2>/dev/null | head -1 || true)"
[ -n "$EXE" ] || { echo "no .exe in $GAME" >&2; exit 1; }
DATA_NAME="${EXE%.exe}_Data"
DATA_SRC="$GAME/$DATA_NAME"
[ -d "$DATA_SRC" ] || { echo "no data dir $DATA_SRC" >&2; exit 1; }

read -r COMPANY PRODUCT <<<"$(awk 'NR==1{c=$0} NR==2{p=$0} END{print c" "p}' "$DATA_SRC/app.info")"
[ -n "$APP_NAME" ] || APP_NAME="$PRODUCT"
[ -n "$BUNDLE_ID" ] || BUNDLE_ID="com.$COMPANY.$(echo "$PRODUCT" | tr -cd '[:alnum:]' | tr 'A-Z' 'a-z')"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${OUT_DIR:-$HERE/build}"
APP="$OUT_DIR/$APP_NAME.app"
EXEC_NAME="$(echo "$PRODUCT" | tr -cd '[:alnum:]._-')"
[ -n "$EXEC_NAME" ] || EXEC_NAME="Game"

echo "==> player template : $VARIATION"
echo "==> game            : $GAME"
echo "==> data            : $DATA_NAME (company=$COMPANY product=$PRODUCT)"
echo "==> app             : $APP"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$APP/Contents/Resources" "$APP/Contents/Plugins"

echo "==> player binaries"
cp -p "$VARIATION/Contents/MacOS/UnityPlayer" "$APP/Contents/MacOS/$EXEC_NAME"
cp -p "$VARIATION/Contents/Frameworks/"*.dylib "$APP/Contents/Frameworks/"
mkdir -p "$APP/Contents/Frameworks/MonoEmbedRuntime/osx"
cp -p "$VARIATION/Contents/Frameworks/"*.dylib "$APP/Contents/Frameworks/MonoEmbedRuntime/osx/"

if [ -d "$SUPPORT_DIR/MonoBleedingEdge" ]; then
  echo "==> mono runtime config (MonoBleedingEdge/etc)"
  cp -R "$SUPPORT_DIR/MonoBleedingEdge" "$APP/Contents/MonoBleedingEdge"
fi
NIB="$SUPPORT_DIR/Source/Player/MacPlayer/MacPlayerEntryPoint/Resources/MainMenu.nib"
[ -d "$NIB" ] && cp -R "$NIB" "$APP/Contents/Resources/" || true

echo "==> built-in shader resources (macOS-compiled, engine loads these from Contents/Resources)"
cp "$VARIATION/Contents/Resources/unity default resources" "$APP/Contents/Resources/" 2>/dev/null || true

echo "==> game data (APFS clone when possible)"
if ! cp -c -R "$DATA_SRC" "$APP/Contents/Resources/Data" 2>/dev/null; then
  cp -R "$DATA_SRC" "$APP/Contents/Resources/Data"
fi
cp "$VARIATION/Contents/Resources/unity default resources" "$APP/Contents/Resources/Data/Resources/unity default resources" 2>/dev/null || true
[ -f "$APP/Contents/Resources/Data/Resources/unity_builtin_extra" ] && \
  cp "$APP/Contents/Resources/Data/Resources/unity_builtin_extra" "$APP/Contents/Resources/" || true

printf 'wait-for-native-debugger=0\nhdr-display-enabled=0\n' > "$APP/Contents/Resources/Data/boot.config"
[ -n "$STEAM_APPID" ] && printf '%s\n' "$STEAM_APPID" > "$APP/Contents/Resources/Data/steam_appid.txt"

echo "==> Info.plist"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key><string>English</string>
	<key>CFBundleExecutable</key><string>$EXEC_NAME</string>
	<key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleName</key><string>$PRODUCT</string>
	<key>CFBundleDisplayName</key><string>$PRODUCT</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>1.0</string>
	<key>CFBundleVersion</key><string>0</string>
	<key>CFBundleSupportedPlatforms</key><array><string>MacOSX</string></array>
	<key>LSMinimumSystemVersion</key><string>10.13.0</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>NSSupportsAutomaticGraphicsSwitching</key><true/>
	<key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoads</key><true/></dict>
	<key>NSMainNibFile</key><string>MainMenu</string>
	<key>NSPrincipalClass</key><string>PlayerApplication</string>
</dict>
</plist>
PLIST

echo "==> ad-hoc signing (arm64 binaries must be signed to run at all)"
for f in "$APP/Contents/Frameworks/"*.dylib "$APP/Contents/Frameworks/MonoEmbedRuntime/osx/"*.dylib "$APP/Contents/Plugins/"*.dylib; do
  [ -f "$f" ] && codesign --force -s - "$f" >/dev/null 2>&1 || true
done
codesign --force --deep -s - "$APP" 2>/dev/null || codesign --force --deep -s - "$APP"

echo "==> done: $APP"
du -sh "$APP"
cat <<EOF

next:
  1. install native plugin shims (gen_plugin_stubs.py) into Contents/Frameworks/MonoEmbedRuntime/osx
  2. patch the player data:
       "$(dirname "$0")/patch_game_data.py" --data "$APP/Contents/Resources/Data" --donor <Donor>.app
  3. run it OUTSIDE ~/Documents / ~/Desktop / ~/Downloads (macOS TCC blocks apps there)
EOF
