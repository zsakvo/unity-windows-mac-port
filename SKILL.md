---
name: unity-windows-mac-port
version: 1
description: Use when a Windows-only game built on a cross-platform engine (Unity Mono above all, and the analogous Godot/GameMaker/Ren'Py runtime-swap cases) needs to run natively on macOS, or when an existing "port" hangs, crashes, renders magenta, resets its settings, or needs a store SDK shimmed. Covers identifying engine version and platform-stamped data, grafting the game's data onto a macOS player of the same version, re-baking Metal shaders, replacing Windows-only native plugins, and diagnosing the macOS-specific failures along the way.
---

# Native macOS port of a Windows Unity (Mono) game

The engine is cross-platform, the *build* is not.  A Unity player is a
platform-specific runtime plus platform-neutral assets plus a few
platform-stamped artefacts.  This skill swaps the runtime and repairs those
artefacts; it produces a real `.app` that runs under Metal with no Wine.

Base directory for every path below: this skill's directory (see
`<skill_resources>`).  Work in a scratch directory, never inside the original
store installation.

## Decide first (≈5 minutes, prevents days of work)

```bash
python3 scripts/recon.py "/path/to/GameDir" --json recon.json
```

Read the report:

* **IL2CPP?** (`Data/Native/GameAssembly.dll`, no real `Data/Managed/*.dll`) →
  **stop**.  Game logic is native Windows code; no data swap can work
  (`references/other-engines.md`).
* **Unity version** – the player module must match it exactly, including edition
  suffixes (`2020.3.24f1c2` ≠ `2020.3.24f1`).
* **Shader platforms** – any `platforms=[4]` (D3D11) shader means phase 4 is
  mandatory.
* **P/Invoke surface** – that list is the phase 5 work plan.
* Engine is not Unity?  See `references/other-engines.md` for the per-stack
  equivalent of these steps (the method transfers; the details do not).

You will need: the game's own data (untouched), the Unity *Mac Build Support
(Mono)* module for its exact version (no licence needed), and – only for phase 4
– an installed Unity editor of that version **with an activated licence**.

## Phase 1 – get a macOS player

```
https://download.unity3d.com/download_unity/<changeset>/MacEditorTargetInstaller/UnitySetup-Mac-Mono-Support-for-Editor-<version>.pkg
https://download.unitychina.cn/download_unity/<changeset>/MacEditorTargetInstaller/…                       (Unity China builds)

xar -xf <pkg> && gzip -dc TargetSupport.pkg.tmp/Payload | cpio -idm --quiet
```

`<changeset>` is the hash in the version string inside the game's
`UnityPlayer.dll` (`2020.3.24f1c2_f3616f7ef48a` → `f3616f7ef48a`).  The module
yields `Variations/macos_{arm64,x64,x64arm64}_{development,nondevelopment}_mono/UnityPlayer.app`
plus `MonoBleedingEdge/etc` and `Source/…/MainMenu.nib`.  Use `macos_x64arm64_*`
for a universal build, `macos_arm64_*` for arm64-only.

## Phase 2 – assemble the bundle

```bash
scripts/make_app.sh --game "/path/GameDir" \
                    --player "/path/to/MacStandaloneSupport" \
                    --out ./build [--steam-appid 1234560]
```

Grafts the game's `*_Data` onto the player skeleton and writes `Info.plist`.
The engine reads built-in shader resources from `Contents/Resources/`, which this
script handles; missing that layout is the classic
`Internal-ErrorShader.shader could not be loaded` crash
(`references/unity-data-format.md` §5).

## Phase 3 – platform stamps (no shaders yet)

```bash
scripts/patch_game_data.py --data "<app>/Contents/Resources/Data" --stamps-only
```

Two int32 stamps decide the graphics device: `BuildSettings.m_GraphicsAPIs`
(Windows `[2]` → macOS `[16]`) and
`GraphicsSettings.m_ShaderDefinesPerShaderCompiler[0].shaderPlatform`
(`4` → `14`).  Leave them and the player creates a **NullGfxDevice** and crashes;
"fix" it with `-force-metal -force-gfx-without-build` and the engine refuses every
shader instead (`references/unity-data-format.md` §3).

## Phase 4 – re-bake the shaders (the real work)

1. `recon.py` prints the `(shader, keywords)` pairs the materials use → write them
   to `needed-shaders.txt`.
2. Collect shader **sources**: Unity built-ins come free; package/SDK shaders are
   copied from the packages; a private shader must be reconstructed from its
   parsed form (property names, pass count, keywords) and marked as approximate.
3. Build the donor with a licensed editor of the **same Unity version**:

   ```bash
   scripts/build_shader_donor.sh --unity <Unity.app/Contents/MacOS/Unity> \
                                 --needed needed-shaders.txt --shaders-src ./shader-sources
   ```

   Run the editor in **GUI mode with `-executeMethod … -quit`**; `-batchmode`
   cannot complete the licence handshake on Personal/Pro seats.
4. Transplant:

   ```bash
   scripts/patch_game_data.py --data "<app>/Contents/Resources/Data" --donor donor-build/Donor.app
   ```

   Whole shader objects, matched by path id then by name, with fallbacks blanked –
   blob-only transplants still report "shader is not supported on this GPU", and
   fallback resolution crashes the loader (`references/shader-rebake.md` §4).

## Phase 5 – native plugins

```bash
scripts/gen_plugin_stubs.py --managed "<app>/Contents/Resources/Data/Managed" --out ./shims
# copy the dylibs into Contents/Frameworks/MonoEmbedRuntime/osx/ and Contents/Plugins/,
# append shims/dllmap.conf inside <configuration> of Contents/MonoBleedingEdge/etc/mono/config,
# then: codesign --force --deep -s - "<app>"
```

Classify each imported module (Windows-only API → no-op shim; portable SDK →
supply its macOS library; boot-gating SDK → satisfy its handshake).  Watch for
games that *quit* when a store SDK is missing, and for settings the SDK is asked
about at startup and then used to overwrite the saved value
(`references/native-plugins.md` §3).

## Phase 6 – verify, then install

```bash
scripts/verify_port.sh "<app>" --screenshot shot.png
```

It launches the app and checks the log for every signature this workflow has
seen: `NullGfxDevice`, `was not built from editor`, `ERROR: Shader …`,
`Internal-ErrorShader`, `DllNotFoundException`,
`Invalid serialized file version`, crashes, early exits.  Expect Metal, zero
shader errors, and a process that stays alive.

Install to `~/Applications` or `/Applications` – **never** `~/Documents`,
`~/Desktop` or `~/Downloads`: TCC blocks an app there from reading its own bundle
and it hangs inside `open()` (`references/macos-host.md` §1).

## The pitfalls that cost the most time

| symptom | cause |
|---|---|
| NullGfxDevice, then a crash in `Material::BuildProperties` | graphics-API stamp still Windows (phase 3) |
| `shader is not supported on this GPU` after transplanting blobs | parsed form not replaced, or donor Unity version ≠ player version |
| crash in `DeferredShaderActions::ApplyDeferredShaderLoadActions` from `ResolveFallback` | shader fallbacks not cleared |
| app "hangs at startup" with no log | app inside a TCC-protected folder |
| app exits a few seconds in | a native SDK boot-gate (`Application.Quit` on `DllNotFoundException`); catch it with `lldb … br set -n exit` |
| user's in-game setting reverts on restart | the game overwrites it from an SDK getter; make the shim answer from the game's own settings file |
| `Invalid serialized file version` | player module and game data are different Unity versions/editions |
| `Failed to activate/update license` from the editor | ran with `-batchmode`; use GUI mode |
| `codesign` complains about `…/etc/mono/4.0` | mono config tree under `Contents/Frameworks/` instead of `Contents/` |

## Reference map

* `references/unity-data-format.md` – player data format, platform stamps, UnityPy
  caveats, bundle layout, obtaining a player without a licence.
* `references/shader-rebake.md` – donor build, transplant mechanics, fallbacks,
  reconstructing private shaders.
* `references/native-plugins.md` – DllImport → dylib shims, dllmap, Tolk-class
  no-ops, Steamworks-class handshake and the derived-settings trap, IL2CPP.
* `references/macos-host.md` – TCC, signing, the diagnostics toolkit (`sample`,
  `.ips`, lldb), how to launch so results are meaningful.
* `references/other-engines.md` – what the same method means for IL2CPP, Godot,
  Unreal, GameMaker, Ren'Py/Löve/Electron, and the ownership/licensing boundaries.
