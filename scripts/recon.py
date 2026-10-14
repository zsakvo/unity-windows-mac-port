#!/usr/bin/env python3
"""
recon.py - survey a Windows Unity game build before attempting a macOS port.

Answers, in one pass, every question that decides the porting strategy:

  * which Unity version built it (and therefore which macOS player to graft on)
  * Mono or IL2CPP
  * how the graphics API / shader compiler platform is stamped in the data
  * which Shader objects exist and whether they carry Metal (platform 14)
  * the complete P/Invoke surface (which native plugins need macOS stand-ins)
  * Addressables content directories (StandaloneWindows64 vs StandaloneOSX)
  * company/product (Player.log + persistentDataPath paths) and the Steam AppID

Usage:
    python3 recon.py "/path/to/GameDir"                 # GameDir contains Game.exe + Game_Data
    python3 recon.py "/path/to/Game_Data"               # or point straight at the data dir
    python3 recon.py ... --json recon.json              # machine-readable copy

Requires: UnityPy (pip install UnityPy) and, for the P/Invoke scan, dnfile
(pip install dnfile).  Missing optional deps degrade to a warning, never a crash.
"""
import argparse
import json
import os
import re
import struct
import sys

PLATFORM_NAMES = {0: "GL", 1: "D3D9", 2: "X360", 3: "PS3", 4: "D3D11", 5: "GLES20",
                  6: "NaCl", 7: "Flash", 8: "D3D11_9x", 9: "GLES3+", 10: "PSP2",
                  11: "PS4", 12: "XboxOne", 13: "PSM", 14: "Metal", 15: "OpenGLCore",
                  16: "N3DS", 17: "WiiU", 18: "Vulkan", 19: "Switch"}


def find_data_dir(root):
    """Accept either the game folder (with <name>_Data) or the data folder itself."""
    root = os.path.abspath(root)
    if os.path.basename(root).endswith("_Data"):
        return os.path.dirname(root), root
    cands = [d for d in sorted(os.listdir(root)) if d.endswith("_Data")]
    if not cands:
        raise SystemExit(f"no *_Data directory inside {root}")
    return root, os.path.join(root, cands[0])


def data_version(data_dir):
    """The Unity version string in the data itself - what the player must match."""
    p = os.path.join(data_dir, "globalgamemanagers")
    if not os.path.exists(p):
        return None
    try:
        import UnityPy
        return UnityPy.load(p).file.unity_version
    except Exception:
        return None


def unity_version(game_dir, data_dir):
    """Unity ships its version string in UnityPlayer.dll / the player executable."""
    for name in ("UnityPlayer.dll", "UnityPlayer.so"):
        p = os.path.join(game_dir, name)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            blob = f.read()
        hits = re.findall(rb"(20\d\d\.\d+\.\d+[abfp]\d+(?:c\d+)?)", blob)
        rev = re.findall(rb"(20\d\d\.\d+\.\d+[abfp]\d+(?:c\d+)?_[0-9a-f]{12})", blob)
        if hits:
            versions = sorted({h.decode() for h in hits})
            return versions, sorted({r.decode() for r in rev})
    return [], []


def build_settings_stamps(data_dir):
    """Read BuildSettings.m_GraphicsAPIs and GraphicsSettings shader platform (raw bytes).

    Both live in globalgamemanagers.  UnityPy cannot parse the China-edition
    PlayerSettings, so the graphics-API list is located by its shape: the last
    int32 of the BuildSettings object, and the shader-platform list right after
    its 1-element vector count.
    """
    out = {}
    ggm = os.path.join(data_dir, "globalgamemanagers")
    if not os.path.exists(ggm):
        return out
    raw = open(ggm, "rb").read()
    try:
        import UnityPy
        env = UnityPy.load(ggm)
        for obj in env.objects:
            if obj.type.name == "BuildSettings":
                off = obj.byte_start + obj.byte_size - 4
                out["graphics_apis"] = list(struct.unpack("<i", raw[off:off + 4]))
                out["buildsettings_offset"] = obj.byte_start
                out["buildsettings_size"] = obj.byte_size
            if obj.type.name == "GraphicsSettings":
                pat = struct.pack("<ii", 1, 4)   # one entry, D3D11
                hits = [i for i in range(obj.byte_start, obj.byte_start + obj.byte_size)
                        if raw.startswith(pat, i)]
                if len(hits) == 1:
                    out["shader_platform_offset"] = hits[0] + 4
                    out["shader_platform"] = 4
                env2 = None
    except ImportError:
        out["_warning"] = "UnityPy not installed; stamps not located"
    except Exception as e:
        out["_warning"] = f"stamp scan failed: {e}"
    return out


def shader_inventory(data_dir, limit_listing=40):
    """List Shader objects per serialized file with their compiled platforms."""
    report = {}
    try:
        import UnityPy
    except ImportError:
        return {"_warning": "UnityPy not installed"}
    files = []
    for root, _dirs, names in os.walk(data_dir):
        if os.path.basename(root) == "Managed":
            continue
        for n in names:
            if n.endswith(".assets") or n in ("globalgamemanagers", "unity default resources",
                                              "unity_builtin_extra", "level0") or n.startswith("level"):
                p = os.path.join(root, n)
                if os.path.getsize(p) < 4 * 1024 ** 3:
                    files.append(p)
    for p in sorted(set(files)):
        try:
            env = UnityPy.load(p)
        except Exception:
            continue
        rows = []
        for obj in env.objects:
            if obj.type.name != "Shader":
                continue
            try:
                d = obj.read_typetree()
            except Exception:
                continue
            name = d.get("m_Name") or d.get("m_ParsedForm", {}).get("m_Name") or "(unnamed)"
            plats = d.get("platforms") or []
            fallback = (d.get("m_ParsedForm") or {}).get("m_FallbackName") or ""
            rows.append({"name": name, "platforms": plats, "fallback": fallback,
                         "has_metal": 14 in plats})
        if rows:
            report[os.path.relpath(p, data_dir)] = rows
    total = sum(len(v) for v in report.values())
    metal = sum(1 for v in report.values() for r in v if r["has_metal"])
    summary = {"files": {k: len(v) for k, v in report.items()},
               "total_shaders": total, "with_metal": metal}
    return {"summary": summary,
            "shaders": {k: v[:limit_listing] for k, v in report.items()}}


def material_usage(data_dir):
    """Which (shader name, keyword set) combinations the game's Materials use.

    The shader donor build must bake exactly these variants, so this is the
    authoritative input for needed-shaders.txt.
    """
    try:
        import UnityPy
    except ImportError:
        return []

    # every shader name we can see, per file basename, so external references resolve
    tables = {}
    data_files = []
    for dirpath, _dirs, names in os.walk(data_dir):
        if os.path.basename(dirpath) == "Managed":
            continue
        for n in names:
            if n.endswith(".assets") or n in ("unity default resources", "unity_builtin_extra"):
                data_files.append(os.path.join(dirpath, n))
    for path in data_files:
        try:
            env = UnityPy.load(path)
        except Exception:
            continue
        table = {}
        for obj in env.objects:
            if obj.type.name != "Shader":
                continue
            try:
                d = obj.read_typetree()
                table[obj.path_id] = d.get("m_Name") or d.get("m_ParsedForm", {}).get("m_Name")
            except Exception:
                pass
        if table:
            tables[os.path.basename(path)] = table

    rows = {}
    for path in data_files:
        if not path.endswith(".assets"):
            continue
        try:
            env = UnityPy.load(path)
        except Exception:
            continue
        ext = {}
        for i, e in enumerate(getattr(env.file, "externals", []) or [], 1):
            p = getattr(e, "path", str(e))
            ext[i] = tables.get(os.path.basename(p), os.path.basename(p))
        local = tables.get(os.path.basename(path), {})
        for obj in env.objects:
            if obj.type.name != "Material":
                continue
            try:
                d = obj.read_typetree()
            except Exception:
                continue
            sh = d.get("m_Shader") or {}
            file_id, path_id = sh.get("m_FileID", 0), sh.get("m_PathID", 0)
            if file_id == 0:
                name = local.get(path_id)
            else:
                ref = ext.get(file_id, "?")
                name = ref.get(path_id) if isinstance(ref, dict) else f"{ref}#{path_id}"
            kw = d.get("m_ShaderKeywords") or d.get("m_ValidKeywords") or ""
            key = (str(name), str(kw))
            rows[key] = rows.get(key, 0) + 1
    return sorted(({"shader": k[0], "keywords": k[1], "materials": v} for k, v in rows.items()),
                  key=lambda r: -r["materials"])


def pinvoke_surface(data_dir):
    """Enumerate DllImport targets per managed assembly (Mono builds only)."""
    managed = os.path.join(data_dir, "Managed")
    result = {}
    if not os.path.isdir(managed):
        return result
    try:
        import dnfile
    except ImportError:
        return {"_warning": "dnfile not installed; P/Invoke scan skipped"}
    for f in sorted(os.listdir(managed)):
        if not f.endswith(".dll"):
            continue
        try:
            pe = dnfile.dnPE(os.path.join(managed, f))
        except Exception:
            continue
        im = getattr(pe.net.mdtables, "ImplMap", None)
        if not im or not im.rows:
            continue
        try:
            mods = [str(r.Name) for r in pe.net.mdtables.ModuleRef.rows]
        except Exception:
            mods = []
        grouped = {}
        for r in im.rows:
            try:
                mod = mods[r.ImportScope.row_index - 1]
            except Exception:
                mod = "?"
            grouped.setdefault(mod, []).append(str(r.ImportName))
        result[f] = {m: sorted(set(v)) for m, v in grouped.items()}
    return result


def layout(data_dir):
    out = {}
    for name in ("app.info", "boot.config", "ScriptingAssemblies.json"):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            out[name] = open(p, errors="ignore").read().strip()
    managed = os.path.join(data_dir, "Managed")
    if os.path.isdir(managed):
        out["mono"] = os.path.exists(os.path.join(managed, "mscorlib.dll"))
        out["managed_dlls"] = len([f for f in os.listdir(managed) if f.endswith(".dll")])
    plugins = os.path.join(data_dir, "Plugins")
    if os.path.isdir(plugins):
        out["native_plugins"] = sorted(
            os.path.relpath(os.path.join(r, f), plugins)
            for r, _d, fs in os.walk(plugins) for f in fs)
    aa = os.path.join(data_dir, "StreamingAssets", "aa")
    if os.path.isdir(aa):
        out["addressables"] = sorted(d for d in os.listdir(aa)
                                     if os.path.isdir(os.path.join(aa, d)))
    return out


def steam_appid(game_dir):
    """AppID from steam_appid.txt, or from a sibling appmanifest_*.acf."""
    p = os.path.join(game_dir, "steam_appid.txt")
    if os.path.exists(p):
        return open(p).read().strip()
    parent = game_dir
    for _ in range(4):
        parent = os.path.dirname(parent)
        if not parent or parent == "/":
            break
        try:
            for f in os.listdir(parent):
                if f.startswith("appmanifest_"):
                    m = re.search(r'appid"?\s*"(\d+)"', open(os.path.join(parent, f), errors="ignore").read())
                    if m:
                        return m.group(1)
        except OSError:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game", help="game folder (with <name>_Data) or the *_Data folder")
    ap.add_argument("--json", help="also write the report as JSON here")
    ap.add_argument("--needed-shaders", help="write <shader>TAB<keywords> lines here (input for the donor build)")
    args = ap.parse_args()

    game_dir, data_dir = find_data_dir(args.game)
    report = {
        "game_dir": game_dir,
        "data_dir": data_dir,
        "data_version": data_version(data_dir),
        "unity_versions": unity_version(game_dir, data_dir),
        "layout": layout(data_dir),
        "stamps": build_settings_stamps(data_dir),
        "steam_appid": steam_appid(game_dir),
    }
    report["shaders"] = shader_inventory(data_dir)
    report["materials"] = material_usage(data_dir)
    report["pinvoke"] = pinvoke_surface(data_dir)

    print("=" * 78)
    print(f"game dir : {game_dir}")
    print(f"data dir : {data_dir}")
    vs, revs = report["unity_versions"]
    dv = report.get("data_version")
    print(f"unity    : data was built by {dv or 'unknown'}"
          f"   (player strings: {', '.join(vs) or '?'}; revisions: {', '.join(revs) or '-'})")
    lay = report["layout"]
    print(f"scripting: {'Mono' if lay.get('mono') else 'IL2CPP or unknown'}"
          f"   managed dlls: {lay.get('managed_dlls', '?')}")
    print(f"app.info : {lay.get('app.info', '-').replace(chr(10), ' / ')}")
    if lay.get("native_plugins"):
        print("plugins  :")
        for p in lay["native_plugins"]:
            print(f"   - {p}")
    if lay.get("addressables"):
        print(f"addressables platform dirs: {lay['addressables']}")
    st = report["stamps"]
    print(f"stamps   : graphics API list = {st.get('graphics_apis')}   "
          f"shader platform offset = {st.get('shader_platform_offset')}")
    if report["steam_appid"]:
        print(f"steam appid: {report['steam_appid']}")

    sh = report["shaders"]
    if "summary" in sh:
        s = sh["summary"]
        print(f"\nshaders  : {s['total_shaders']} objects in {len(s['files'])} file(s), "
              f"{s['with_metal']} with Metal(14)")
        for f, n in s["files"].items():
            print(f"   - {f}: {n} shader(s)")
    else:
        print(f"\nshaders  : {sh.get('_warning')}")

    print("\nmaterials (shader / keywords -> count)  [needed for the donor build]:")
    for row in report["materials"][:25]:
        print(f"   {row['materials']:4}  {row['shader']}   keywords={row['keywords']!r}")

    pv = report["pinvoke"]
    if pv and "_warning" not in pv:
        print("\nP/Invoke surface:")
        for asm, mods in pv.items():
            for mod, fns in mods.items():
                print(f"   {asm}: {mod} ({len(fns)} functions)  e.g. {fns[:3]}")
    elif pv:
        print(f"\nP/Invoke : {pv.get('_warning')}")

    print("\nnext steps:")
    print("  * Mono + shaders without Metal(14) -> skills/unity-windows-mac-port (this skill)")
    print("  * IL2CPP -> a data swap will not work; the game needs its own macOS build")
    if args.needed_shaders:
        lines, seen = [], set()
        for row in report["materials"]:
            if not row["shader"] or row["shader"] == "None":
                continue
            line = f'{row["shader"]}\t{row["keywords"]}'
            if line in seen:
                continue
            seen.add(line)
            lines.append(line)
        open(args.needed_shaders, "w").write("\n".join(lines) + "\n")
        print(f"wrote {args.needed_shaders} ({len(lines)} shader/keyword combinations)")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    sys.exit(main())
