#!/usr/bin/env python3
"""
patch_game_data.py - make a Windows Unity player-data directory acceptable to a
macOS player.

Three things in the data are stamped for the platform the game was built for:

1. `BuildSettings.m_GraphicsAPIs` (in globalgamemanagers) - [2] = Direct3D11 on
   Windows.  A macOS build carries [16].  The player picks its graphics device
   from this list; without the patch it falls back to NullGfxDevice (or needs
   -force-metal -force-gfx-without-build, which disables shaders entirely).
2. `GraphicsSettings.m_ShaderDefinesPerShaderCompiler` - the shader compiler
   platform stamp (4 = D3D11, 14 = Metal).
3. Every Shader object only carries D3D11 sub-programs.  macOS needs Metal;
   the blobs (and, crucially, the parsed form that goes with them) are
   transplanted from a macOS build made with the same Unity version - see
   transplant_shaders.py and build_shader_donor.sh.

Usage:
  patch_game_data.py --data "<App>.app/Contents/Resources/Data" --donor <Donor>.app
  patch_game_data.py --data ... --donor ... --restore-from "<Game>_Data"   # start clean
  patch_game_data.py --data ... --donor ... --blob-only                    # do not replace parsed forms
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
BLOB_FIELDS_HINT = "platforms/offsets/compressedLengths/decompressedLengths/compressedBlob"


def load_unitypy():
    try:
        import UnityPy  # noqa
        return UnityPy
    except ImportError:
        sys.exit("UnityPy is required: pip install UnityPy")


def objects(path, type_name, UnityPy):
    env = UnityPy.load(path)
    return env, [o for o in env.objects if o.type.name == type_name]


def patch_graphics_settings(ggm, UnityPy):
    """Raw byte patch: the shader-platform int of the single defines entry."""
    _env, objs = objects(ggm, "GraphicsSettings", UnityPy)
    if not objs:
        print("  ! no GraphicsSettings object")
        return False
    raw = bytearray(open(ggm, "rb").read())
    for obj in objs:
        pat = struct.pack("<ii", 1, 4)
        hits = [i for i in range(obj.byte_start, obj.byte_start + obj.byte_size)
                if raw.startswith(pat, i)]
        if len(hits) == 1:
            off = hits[0] + 4
            raw[off:off + 4] = struct.pack("<i", 14)
            open(ggm, "wb").write(bytes(raw))
            print("  GraphicsSettings shaderPlatform 4 -> 14 (Metal)")
            return True
        pat14 = struct.pack("<ii", 1, 14)
        if any(raw.startswith(pat14, i) for i in range(obj.byte_start, obj.byte_start + obj.byte_size)):
            print("  GraphicsSettings shaderPlatform already 14")
            return False
    print("  ! could not locate the shader platform entry")
    return False


def patch_build_settings(ggm, UnityPy):
    """Raw byte patch: m_GraphicsAPIs is the last field of BuildSettings."""
    _env, objs = objects(ggm, "BuildSettings", UnityPy)
    if not objs:
        print("  ! no BuildSettings object")
        return False
    obj = objs[0]
    raw = bytearray(open(ggm, "rb").read())
    off = obj.byte_start + obj.byte_size - 4
    value = struct.unpack("<i", raw[off:off + 4])[0]
    if value == 16:
        print("  BuildSettings m_GraphicsAPIs already [16]")
        return False
    raw[off:off + 4] = struct.pack("<i", 16)
    open(ggm, "wb").write(bytes(raw))
    print(f"  BuildSettings m_GraphicsAPIs [{value}] -> [16] (macOS)")
    return True


def donor_files(donor, max_size=256 * 1024 ** 2):
    """Serialized files inside a macOS-built player that can serve as shader donors."""
    if donor.endswith(".app"):
        root = os.path.join(donor, "Contents", "Resources")
    else:
        root = donor
    found = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if not (n.endswith(".assets") or n in ("unity_builtin_extra", "unity default resources")):
                continue
            p = os.path.join(dirpath, n)
            if os.path.getsize(p) <= max_size:
                found.append(p)
    # prefer the file that actually carries baked shaders
    found.sort(key=lambda p: (0 if os.path.basename(p) == "unity_builtin_extra" else 1, -os.path.getsize(p)))
    return found


def targets(data_dir):
    """Player data files that can contain Shader objects."""
    out = []
    for dirpath, _dirs, names in os.walk(data_dir):
        if os.path.basename(dirpath) == "Managed":
            continue
        for n in names:
            if n.endswith(".assets") or n == "unity_builtin_extra":
                out.append(os.path.join(dirpath, n))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="the .app's Contents/Resources/Data directory")
    ap.add_argument("--donor", help="macOS-built player (.app) or a directory of donor data (omit with --stamps-only)")
    ap.add_argument("--stamps-only", action="store_true",
                    help="only patch the graphics API / shader platform stamps (phase 3, before a donor exists)")
    ap.add_argument("--restore-from", help="original Windows *_Data directory (restore before patching)")
    ap.add_argument("--blob-only", action="store_true",
                    help="copy only the compiled blob fields (usually NOT enough: the parsed form must match)")
    ap.add_argument("--keep-fallback", action="store_true",
                    help="keep m_ParsedForm.m_FallbackName (resolving fallbacks can crash 2020.3)")
    args = ap.parse_args()

    UnityPy = load_unitypy()
    data = os.path.abspath(args.data)
    ggm = os.path.join(data, "globalgamemanagers")

    if args.restore_from:
        src = os.path.abspath(args.restore_from)
        n = 0
        for t in targets(data) + [ggm]:
            rel = os.path.relpath(t, data)
            s = os.path.join(src, rel)
            if os.path.exists(s):
                shutil.copy2(s, t)
                n += 1
        print(f"restored {n} data file(s) from {src}")

    print("1) graphics API stamp")
    patch_build_settings(ggm, UnityPy)
    print("2) shader compiler platform stamp")
    patch_graphics_settings(ggm, UnityPy)

    if args.stamps_only:
        print("done (stamps only; shaders still need a donor - see references/shader-rebake.md)")
        return 0
    if not args.donor:
        sys.exit("--donor is required unless --stamps-only is used")
    donors = donor_files(args.donor)
    if not donors:
        sys.exit(f"no donor shader files under {args.donor}")
    print("3) shader transplant")
    print("   donors: " + ", ".join(os.path.basename(d) for d in donors))
    failures = 0
    for t in targets(data):
        cmd = [PY, os.path.join(HERE, "transplant_shaders.py")]
        if not args.blob_only:
            cmd.append("--whole")
            if not args.keep_fallback:
                cmd.append("--clear-fallback")
        for d in donors:
            cmd += ["--donor", d]
        cmd.append(t)
        r = subprocess.run(cmd, capture_output=True, text=True)
        line = (r.stdout.strip().splitlines() or ["(no output)"])[-3:]
        print(f"   {os.path.relpath(t, data)}: " + " | ".join(l.strip() for l in line))
        failures += r.returncode != 0

    # the engine reads built-in resources from Contents/Resources on macOS
    app = os.path.dirname(os.path.dirname(data))
    bie = os.path.join(data, "Resources", "unity_builtin_extra")
    if os.path.exists(bie):
        dst = os.path.join(app, "Resources", "unity_builtin_extra")
        shutil.copy2(bie, dst)
        print(f"4) synced {os.path.relpath(dst, app)}")
    udr = os.path.join(data, "Resources", "unity default resources")
    if os.path.exists(udr):
        dst = os.path.join(app, "Resources", "unity default resources")
        shutil.copy2(udr, dst)
        print("   synced Contents/Resources/unity default resources")

    print("done" + (f" ({failures} file(s) with missing donor blobs)" if failures else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
