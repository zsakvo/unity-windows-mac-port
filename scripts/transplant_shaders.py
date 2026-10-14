#!/usr/bin/env python3
"""
transplant_shaders.py - give the Windows-built shader objects the Metal (and
OpenGLCore) sub-programs that a macOS player needs.

A Unity player stores, per Shader object, one compiled blob per target graphics
API (the `platforms` array; 4 = D3D11, 14 = Metal, 15 = OpenGLCore).  A Windows
build therefore carries D3D11 data only, and a macOS player refuses to use such
a shader:

    Desired shader compiler platform 14 is not available in shader blob
    ERROR: Shader <name> shader is not supported on this GPU ...

This tool copies the blob fields (`platforms`, `offsets`, `compressedLengths`,
`decompressedLengths`, `compressedBlob`) from files that were built for macOS,
matching objects first by path id (exact, used for Unity's built-in resources,
whose ids are stable) and then by shader name (used for package shaders, whose
ids are per-project), and writes the target file back.

Example:
  transplant_shaders.py --whole --clear-fallback \\
      --donor donor-build/Donor.app/Contents/Resources/Data/Resources/unity_builtin_extra \\
      --donor donor-build/Donor.app/Contents/Resources/Data/globalgamemanagers.assets \\
      "</App>.app/Contents/Resources/Data/resources.assets"
"""
import argparse
import os
import sys

import UnityPy

BLOB_FIELDS = ("platforms", "offsets", "compressedLengths", "decompressedLengths", "compressedBlob")
PLATFORM_NAMES = {4: "D3D11", 5: "GLES20", 9: "GLES3+", 14: "Metal", 15: "OpenGLCore", 18: "Vulkan"}


def read_shaders(path):
    """returns (by_id, by_name) of shader typetrees for one file"""
    env = UnityPy.load(path)
    by_id, by_name = {}, {}
    for obj in env.objects:
        if obj.type.name != "Shader":
            continue
        try:
            d = obj.read_typetree()
        except Exception:
            continue
        name = d.get("m_Name") or d.get("m_ParsedForm", {}).get("m_Name")
        by_id[obj.path_id] = (name, d)
        if name:
            prev = by_name.get(name)
            if prev is None or (len(d.get("platforms") or []) > len(prev[1].get("platforms") or [])):
                by_name[name] = (obj.path_id, d)
    return by_id, by_name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--donor", action="append", required=True,
                    help="macOS-built file (repeatable); earlier donors win")
    ap.add_argument("target", help="file to patch (in place unless -o is given)")
    ap.add_argument("-o", "--output", help="write result here instead of overwriting target")
    ap.add_argument("--platform", type=int, default=14, help="wanted platform id (default 14 = Metal)")
    ap.add_argument("--whole", action="store_true",
                    help="replace the entire shader object (parsed form included) instead of only the "
                         "compiled blob fields; needed when the target's parsed form does not match the blob")
    ap.add_argument("--no-name-match", action="store_true", help="only match by path id")
    ap.add_argument("--clear-fallback", action="store_true",
                    help="blank out m_ParsedForm.m_FallbackName (fallbacks are unnecessary once every "
                         "shader has a working Metal program, and resolving them crashes 2020.3 here)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    donors = []
    for p in args.donor:
        if not os.path.exists(p):
            print(f"donor missing: {p}", file=sys.stderr)
            return 2
        by_id, by_name = read_shaders(p)
        with_metal = sum(1 for _, d in by_id.values() if args.platform in (d.get("platforms") or []))
        print(f"donor {os.path.basename(p)}: {len(by_id)} shaders, {with_metal} with platform {args.platform}")
        donors.append((by_id, by_name))

    env = UnityPy.load(args.target)
    patched = already = 0
    failures = []
    for obj in env.objects:
        if obj.type.name != "Shader":
            continue
        d = obj.read_typetree()
        name = d.get("m_Name") or d.get("m_ParsedForm", {}).get("m_Name")
        cur = d.get("platforms") or []
        if args.platform in cur and not args.whole:
            already += 1
            continue

        found = None
        source = None
        # 1) exact path id (stable for Unity's built-in resources)
        for by_id, by_name in donors:
            if obj.path_id in by_id and args.platform in (by_id[obj.path_id][1].get("platforms") or []):
                found = by_id[obj.path_id][1]
                source = f"pathid {obj.path_id}"
                break
        # 2) shader name (package shaders get per-project ids)
        if found is None and not args.no_name_match and name:
            for by_id, by_name in donors:
                if name in by_name and args.platform in (by_name[name][1].get("platforms") or []):
                    found = by_name[name][1]
                    source = f"name {name!r}"
                    break

        if found is None:
            failures.append((name, cur))
            continue

        if args.whole:
            # replace every field of the shader object (parsed form included)
            d = dict(found)
            if args.clear_fallback and isinstance(d.get("m_ParsedForm"), dict):
                d["m_ParsedForm"] = dict(d["m_ParsedForm"])
                d["m_ParsedForm"]["m_FallbackName"] = ""
        else:
            for f in BLOB_FIELDS:
                if f in found:
                    d[f] = found[f]
        if not args.dry_run:
            obj.save_typetree(d)
        patched += 1
        if args.verbose:
            print(f"  patched {name!r} [{source}]: {cur} -> {found.get('platforms')}")

    print(f"target {os.path.basename(args.target)}: patched={patched} already-had-platform={already} no-donor={len(failures)}")
    for name, cur in failures:
        print(f"    ! no macOS blob for {name!r} (had {cur})")

    if not args.dry_run and patched:
        out = args.output or args.target
        data = env.file.save()
        with open(out, "wb") as fh:
            fh.write(data)
        print(f"wrote {out} ({len(data)} bytes)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
