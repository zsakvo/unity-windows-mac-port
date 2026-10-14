# Scope: what transfers to other engines and stacks

The method in this skill is *"replace the platform-specific runtime and
pre-compiled artifacts, keep the platform-neutral assets"*.  Where a stack stores
login/shader/native code decides whether that is possible.

## The general recipe

For any engine ask four questions:

1. **Runtime binary** – is there a macOS runtime build of the same version?
   (Unity: the PlaybackEngine player; Godot: an export template; GameMaker: a
   runner; Ren'Py/Löve/Electron: a plain per-platform runtime.)
2. **Data format** – does the data carry platform stamps?  (Unity:
   `BuildSettings.m_GraphicsAPIs`, shader `platforms`; most script-driven engines:
   nothing.)
3. **Pre-compiled code/data** – shaders, IL, bytecode, cooked assets: are they
   per-platform?  (Unity: shaders yes, IL no; Unreal: shaders yes; Godot 4: shader
   cache outside the `.pck`.)
4. **Native plugins** – which `DllImport`/GDExtension/native modules must be
   replaced, and which are optional?

## Per-stack notes

| stack | feasible? | notes |
|---|---|---|
| **Unity, Mono** | yes | this skill.  Managed IL is portable; re-bake shaders; shim/replace native plugins |
| **Unity, IL2CPP** | no | game logic is native Windows code (`Data/Native/GameAssembly.dll`); no data swap |
| **Unity asset bundles / Addressables** | partial | bundles have their own `targetPlatform` and shader programs; patch or re-bake them |
| **Godot 3/4** | usually yes | take the macOS editor/export template of the same version and run the game's `.pck`; GDExtension `.dll`s need macOS equivalents.  Godot 4 shader caches recompile on first run |
| **Unreal** | rarely | cooked content is tied to the engine build and shaders are precompiled per platform; only the project (re-cook) or the vendor's macOS build works |
| **GameMaker** | often | `data.win` (or `.ios`/`.unx`) loads in a macOS runner of the same runtime version; watch the runner's version match |
| **Ren'Py / Löve / Electron / NW.js** | easily | script+assets are platform neutral: ship the macOS runtime of the same version next to the game's script/asset archive |
| **Custom in-house engines** | depends | if the sources are available, rebuild the engine for macOS and keep the assets; otherwise treat like Unreal |

## Boundaries worth stating up front

* Only for a game you own, for personal use.  Do not redistribute the assembled
  port or the engine binaries; keep the original store installation untouched.
* Preserve the store's DRM/licensing behaviour rather than circumventing it: an
  SDK shim exists to let the game *boot*, not to bypass ownership checks.  If the
  game needs Steam (or another store) for features the user wants, install that
  store's macOS client and the real library instead.
* A port done this way is a *compatibility* build: private shaders may be
  approximations and store features may be missing.  When the developer ships a
  macOS build (or the project sources), prefer it – a polite request to the
  developer is often the fastest correct route.
* Anything generated from the game (shader blobs, decompiled code, donor builds)
  stays local to the user's machine.
