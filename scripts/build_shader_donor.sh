#!/bin/bash
# ---------------------------------------------------------------------------
# build_shader_donor.sh - build a macOS "shader donor" so that a Windows Unity
# build's D3D11-only shaders can be replaced with Metal ones.
#
#   The donor is a throw-away Unity project containing the same shaders the game
#   uses.  Building it for macOS makes Unity bake Metal sub-programs, which are
#   then transplanted into the game's data (patch_game_data.py).
#
# Usage:
#   build_shader_donor.sh --unity "/Applications/Unity/.../Unity.app/Contents/MacOS/Unity" \
#                         --needed needed-shaders.txt \
#                         --shaders-src ./shader-sources \
#                         [--project ./shader-project] [--version 2020.3.24f1]
#
#   needed-shaders.txt   : lines "<Shader Name>\t<keywords>" (recon.py prints these)
#   shaders-src          : directory tree copied into Assets/ (package shader
#                          sources, Live2D SDK shaders, your own reconstructions)
#
# IMPORTANT
#   * Use a Unity editor of the SAME version that built the game: the shader blob
#     format belongs to the engine version.
#   * Do NOT pass -batchmode.  Batch mode takes Unity's headless entitlement
#     path, which fails the licence handshake on Personal seats; the GUI path
#     (with -quit) works and is what this script uses.
#   * #pragma shader_feature is rewritten to #pragma multi_compile in the copies
#     so that every variant gets baked, not only the ones the donor project uses.
# ---------------------------------------------------------------------------
set -euo pipefail

UNITY=""; NEEDED=""; SHADERS_SRC=""; PROJECT=""; VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --unity) UNITY="$2"; shift 2 ;;
    --needed) NEEDED="$2"; shift 2 ;;
    --shaders-src) SHADERS_SRC="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$UNITY" ] && [ -n "$NEEDED" ] || { echo "need --unity and --needed" >&2; exit 2; }
[ -x "$UNITY" ] || { echo "no Unity editor at $UNITY" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="${PROJECT:-$HERE/shader-project}"

# Unity version for ProjectVersion.txt: ask the editor when not supplied
if [ -z "$VERSION" ]; then
  VERSION="$("$UNITY" -version 2>/dev/null | head -1 || true)"
  [ -n "$VERSION" ] || VERSION="2020.3.24f1"
fi

echo "==> project $PROJECT  (Unity $VERSION)"
mkdir -p "$PROJECT/Assets/Editor" "$PROJECT/Assets/Resources/Donor" "$PROJECT/Assets/Scenes" "$PROJECT/Packages" "$PROJECT/ProjectSettings"
printf 'm_EditorVersion: %s\n' "$VERSION" > "$PROJECT/ProjectSettings/ProjectVersion.txt"
cat > "$PROJECT/Packages/manifest.json" <<'JSON'
{
  "dependencies": {
    "com.unity.ugui": "1.0.0",
    "com.unity.modules.audio": "1.0.0",
    "com.unity.modules.imageconversion": "1.0.0",
    "com.unity.modules.imgui": "1.0.0",
    "com.unity.modules.jsonserialize": "1.0.0",
    "com.unity.modules.ui": "1.0.0",
    "com.unity.modules.uielements": "1.0.0"
  }
}
JSON

cp "$NEEDED" "$PROJECT/needed-shaders.txt"
cp "$HERE/BuildDonor.cs" "$PROJECT/Assets/Editor/BuildDonor.cs"

if [ -n "$SHADERS_SRC" ] && [ -d "$SHADERS_SRC" ]; then
  echo "==> copying shader sources from $SHADERS_SRC"
  cp -R "$SHADERS_SRC/." "$PROJECT/Assets/"
fi

echo "==> rewriting shader_feature -> multi_compile in the copies (variant coverage)"
find "$PROJECT/Assets" -name "*.shader" -print0 2>/dev/null | while IFS= read -r -d '' f; do
  sed -i '' -e 's/#pragma shader_feature_local_fragment/#pragma multi_compile_local_fragment/g' \
            -e 's/#pragma shader_feature_local_vertex/#pragma multi_compile_local_vertex/g' \
            -e 's/#pragma shader_feature_fragment/#pragma multi_compile_fragment/g' \
            -e 's/#pragma shader_feature_vertex/#pragma multi_compile_vertex/g' \
            -e 's/#pragma shader_feature_local/#pragma multi_compile_local/g' \
            -e 's/#pragma shader_feature/#pragma multi_compile/g' "$f"
done

echo "==> running Unity (GUI mode + -quit; batch mode cannot complete licensing)"
LOG="$HERE/donor-build.log"
rm -f "$LOG"
"$UNITY" -projectPath "$PROJECT" -executeMethod BuildDonor.BuildFromCommandLine -quit -logFile "$LOG" || true
echo "    log: $LOG"
grep -E "\[donor\]|error CS|Build result" "$LOG" | tail -10 || true

DONOR="$HERE/donor-build/Donor.app"
if [ -d "$DONOR" ]; then
  echo "==> donor built: $DONOR"
  ls "$DONOR/Contents/Resources/Data/Resources/" 2>/dev/null || true
  cat <<EOF

next:
  "$HERE/patch_game_data.py" --data "<App>.app/Contents/Resources/Data" --donor "$DONOR"
EOF
else
  echo "!! donor build failed - inspect $LOG" >&2
  exit 1
fi
