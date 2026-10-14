#!/usr/bin/env python3
"""
gen_plugin_stubs.py - generate macOS stand-ins for a Mono Unity game's Windows
native plugins, plus the mono dllmap entries that wire them up.

A Windows Unity build imports native libraries by name (Tolk.dll, steam_api64,
Live2DCubismCore, ...).  On macOS Unity/Mono looks for `lib<name>.dylib`,
`<name>.dylib` or `<name>.bundle` and honours `dllmap` entries in
`Contents/MonoBleedingEdge/etc/mono/config`.  For plugins whose functionality is
Windows-only (screen readers) or optional (Steam when you only need the game to
boot) a no-op shim is enough.

Usage:
  gen_plugin_stubs.py --managed "/path/Game_Data/Managed" --out ./shims
  gen_plugin_stubs.py --managed ".../Managed" --out ./shims \
      --steam-lang-config "$HOME/Library/Application Support/<bundle-id>/GameConfigData.json" \
      --steam-lang-field Language --steam-lang-map 0=schinese,1=japanese,2=english,3=koreana

Outputs, per imported module:
  shims/<module>.c, shims/lib<module>.dylib (if clang is present)
  shims/dllmap.conf          - append to the app's mono config
  shims/README.txt           - where to copy the dylibs

Steam modules get a smarter stub:  SteamInternal_SteamAPI_Init returns
k_ESteamAPIInitResult_OK(0), SteamInternal_CreateInterface / GetHSteam* /
*GetISteam* / *FindOrCreate* return a non-NULL handle (Steamworks.NET validates
~30 pointers in CSteamAPIContext.Init()), everything else returns 0, and the
game-language getters answer from the game's own settings file so an in-game
language choice survives restarts.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------- extraction ---

BCL_PREFIXES = ("System.", "Mono.", "mscorlib", "netstandard", "Microsoft.", "Novell.")


def module_exports(managed_dir):
    """{module_name: {"exports": [...], "from": {assembly, ...}}} from every assembly."""
    try:
        import dnfile
    except ImportError:
        sys.exit("dnfile is required: pip install dnfile")
    modules = {}
    for name in sorted(os.listdir(managed_dir)):
        if not name.endswith(".dll"):
            continue
        try:
            pe = dnfile.dnPE(os.path.join(managed_dir, name))
        except Exception:
            continue
        im = getattr(pe.net.mdtables, "ImplMap", None)
        if not im or not im.rows:
            continue
        try:
            refs = [str(r.Name) for r in pe.net.mdtables.ModuleRef.rows]
        except Exception:
            refs = []
        for r in im.rows:
            try:
                mod = refs[r.ImportScope.row_index - 1]
            except Exception:
                continue
            entry = modules.setdefault(mod, {"exports": set(), "from": set()})
            entry["exports"].add(str(r.ImportName))
            entry["from"].add(name)
    return {m: {"exports": sorted(v["exports"]), "from": sorted(v["from"])}
            for m, v in modules.items()}


def dylib_name(module):
    """Turn a Windows library name into the file name macOS/Mono should find.

    Handles BCL style entries such as "/System/Library/Frameworks/…/Carbon" and
    "foo.dll" alike, and keeps the result usable as a file name.
    """
    base = os.path.basename(module)              # drop framework paths
    for suffix in (".dll", ".so", ".dylib", ".drv"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base) or "plugin"
    base = base if base.startswith("lib") else "lib" + base
    return base


def is_framework_path(module):
    """DllImports that name an absolute framework path need no shim on macOS."""
    return module.startswith("/") or ".framework/" in module


# -------------------------------------------------------------------- stubs ----

HEADER = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static FILE *log_file(void)
{
    static FILE *f = NULL;
    if (!f) {
        const char *p = getenv("PLUGIN_STUB_LOG");
        f = fopen(p ? p : "/tmp/plugin_stub.log", "a");
    }
    return f;
}

static void note(const char *name)
{
    FILE *f = log_file();
    if (f) { fprintf(f, "%s\n", name); fflush(f); }
}

static uint64_t dummy_handle = 0x5717B00000000001ull;

#define STUB0(name)      uint64_t name(void) { note(#name); return 0; }
#define STUB_HANDLE(name) uint64_t name(void) { note(#name); return (uint64_t)&dummy_handle; }
#define STUB_TRUE(name)  uint64_t name(void) { note(#name); return 1; }
'''

STEAM_HELPERS = r'''
/* ---- Steamworks handshake helpers -------------------------------------- */

static const char *config_path(void)
{
    static char path[1200];
    const char *p = getenv("STEAM_STUB_CONFIG");
    if (p) { snprintf(path, sizeof(path), "%s", p); return path; }
    const char *home = getenv("HOME");
    snprintf(path, sizeof(path), "%s/Library/Application Support/%s", home ? home : ".",
             getenv("STEAM_STUB_APPID_DIR") ? getenv("STEAM_STUB_APPID_DIR") : "");
    return path;
}

static const char *game_language(void)
{
    static char buf[32];
    strcpy(buf, "english");
    FILE *f = fopen(config_path(), "rb");
    if (f) {
        char data[8192];
        size_t n = fread(data, 1, sizeof(data) - 1, f);
        fclose(f);
        data[n] = 0;
        const char *field = getenv("STEAM_STUB_LANG_FIELD");
        char pattern[64];
        snprintf(pattern, sizeof(pattern), "\"%s\"", field ? field : "Language");
        char *p = strstr(data, pattern);
        if (p && (p = strchr(p, ':')) != NULL) {
            char *map = getenv("STEAM_STUB_LANG_MAP");
            int value = atoi(p + 1);
            if (map) {
                char *copy = strdup(map), *tok, *save = NULL;
                for (tok = strtok_r(copy, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
                    char *eq = strchr(tok, '=');
                    if (eq && atoi(tok) == value) { snprintf(buf, sizeof(buf), "%s", eq + 1); break; }
                }
                free(copy);
            }
        }
    }
    return buf;
}

static void note_lang(const char *name)
{
    FILE *f = log_file();
    if (f) { fprintf(f, "%s -> %s\n", name, game_language()); fflush(f); }
}
'''


def steam_body(name):
    if name == "SteamAPI_RestartAppIfNecessary":
        return f'uint64_t {name}(void) {{ note("{name}"); return 0; /* do not relaunch via Steam */ }}'
    if name == "SteamInternal_SteamAPI_Init":
        return f'uint64_t {name}(void) {{ note("{name}"); return 0; /* k_ESteamAPIInitResult_OK */ }}'
    if name in ("SteamInternal_CreateInterface", "SteamAPI_GetHSteamUser", "SteamAPI_GetHSteamPipe") \
            or "GetISteam" in name or "FindOrCreate" in name:
        return f'STUB_HANDLE({name})'
    if name in ("SteamAPI_ISteamApps_GetCurrentGameLanguage", "SteamAPI_ISteamUtils_GetSteamUILanguage"):
        return f'uint64_t {name}(void) {{ note_lang("{name}"); return (uint64_t)game_language(); }}'
    if name == "SteamAPI_ISteamApps_GetAvailableGameLanguages":
        return f'uint64_t {name}(void) {{ note("{name}"); return (uint64_t)getenv("STEAM_STUB_LANGS"); }}'
    if name == "SteamAPI_IsSteamRunning":
        return f'STUB_TRUE({name})'
    return f'STUB0({name})'


def generic_body(name, module):
    """A few APIs need a truthy answer for a game to continue past a boot check."""
    if re.search(r"(?i)(Silence|IsSupported|IsRunning|IsInitialized|HasSpeech|HasBraille)", name):
        return f'STUB0({name})'
    return f'STUB0({name})'


def emit(module, exports, out_dir, kind):
    c_path = os.path.join(out_dir, dylib_name(module) + ".c")
    body = [HEADER]
    if kind == "steam":
        body.append(STEAM_HELPERS)
    body.append(f'/* {module}: {len(exports)} entry points */')
    for name in exports:
        body.append(steam_body(name) if kind == "steam" else generic_body(name, module))
    open(c_path, "w").write("\n".join(body) + "\n")
    dylib = os.path.join(out_dir, dylib_name(module) + ".dylib")
    cmd = ["clang", "-O1", "-dynamiclib", "-arch", "arm64", "-arch", "x86_64",
           "-o", dylib, c_path]
    if shutil.which("clang"):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return c_path, dylib
        return c_path, None
    return c_path, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--managed", required=True, help="the game's *_Data/Managed directory")
    ap.add_argument("--out", required=True, help="directory for generated shims")
    ap.add_argument("--only", action="append", help="restrict to these modules (repeatable)")
    ap.add_argument("--steam-lang-config", help="JSON settings file the Steam stub reads for the language")
    ap.add_argument("--steam-lang-field", default="Language")
    ap.add_argument("--steam-lang-map", default="0=schinese,1=japanese,2=english,3=koreana")
    ap.add_argument("--include-bcl", action="store_true",
                    help="also shim modules imported only by BCL assemblies (gdiplus, user32, kernel32, libX11, ...)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    modules = module_exports(args.managed)
    if args.only:
        modules = {m: v for m, v in modules.items() if any(o.lower() in m.lower() for o in args.only)}
    if not modules:
        sys.exit("no DllImport modules found (is this an IL2CPP build?)")

    dllmaps, built, skipped = [], [], []
    seen_names = set()
    print("imported native modules:")
    for module, meta in modules.items():
        exports, froms = meta["exports"], meta["from"]
        if is_framework_path(module):
            skipped.append((module, "absolute framework path"))
            continue
        if not args.include_bcl and all(f.startswith(BCL_PREFIXES) for f in froms):
            skipped.append((module, "only used by BCL assemblies: " + ", ".join(froms[:2])))
            continue
        name = dylib_name(module)
        if name in seen_names:
            skipped.append((module, "duplicate output name " + name))
            continue
        seen_names.add(name)
        is_steam = bool(re.search(r"(?i)steam", module))
        kind = "steam" if is_steam else "generic"
        c_path, dylib = emit(module, exports, args.out, kind)
        print(f"  {module:28} {len(exports):5} functions <- {','.join(froms)[:28]:28} -> {os.path.basename(c_path)}"
              f"{' + ' + os.path.basename(dylib) if dylib else ' (compile with clang to get a dylib)'}"
              f"   [{'steam handshake' if is_steam else 'no-op'}]")
        dllmaps.append(f'\t<dllmap dll="{module}" target="{name}.dylib" />')
        bare = module[:-4] if module.lower().endswith(".dll") else module
        if bare != module:
            dllmaps.append(f'\t<dllmap dll="{bare}" target="{name}.dylib" />')
        if dylib:
            built.append(os.path.basename(dylib))

    if skipped:
        print("\nskipped:")
        for m, why in skipped:
            print(f"  {m:34} {why}")

    conf = os.path.join(args.out, "dllmap.conf")
    open(conf, "w").write("\n".join(dllmaps) + "\n")

    readme = [
        "Generated by gen_plugin_stubs.py",
        "",
        "1. copy the .dylib files into the app bundle:",
        "     <App>.app/Contents/Frameworks/MonoEmbedRuntime/osx/",
        "     <App>.app/Contents/Plugins/",
        "   (Mono's macOS search path for DllImport is MonoEmbedRuntime/osx;",
        "    Unity also scans Contents/Plugins.)",
        "",
        "2. append dllmap.conf INSIDE the <configuration> element of",
        "     <App>.app/Contents/MonoBleedingEdge/etc/mono/config",
        "   (keep a copy of the file as etc/config too).",
        "",
        "3. re-sign:  codesign --force --deep -s - <App>.app",
        "",
        "Environment variables understood by the generated stubs:",
        "   PLUGIN_STUB_LOG   where to append the call log (default /tmp/plugin_stub.log)",
        "   STEAM_STUB_CONFIG path to the game's settings JSON (Steam language source)",
        "   STEAM_STUB_LANG_FIELD / STEAM_STUB_LANG_MAP  field name and enum->Steam language mapping",
        "",
        "Dylibs built: " + (", ".join(built) if built else "(none - clang missing)"),
    ]
    open(os.path.join(args.out, "README.txt"), "w").write("\n".join(readme) + "\n")
    print(f"\nwrote {conf} and README.txt in {args.out}")


if __name__ == "__main__":
    sys.exit(main())
