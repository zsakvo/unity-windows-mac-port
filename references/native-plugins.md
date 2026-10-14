# Native plugins: making Windows-only DLLs harmless on macOS

Mono resolves `[DllImport("Foo")]` at runtime.  On macOS it looks for
`libFoo.dylib`, `Foo.dylib`, `Foo.bundle` and honours `dllmap` entries, searching
among others `Contents/Frameworks/MonoEmbedRuntime/osx/` and
`Contents/PlugIns/` inside the app bundle.  A missing library raises
`DllNotFoundException` **inside the game's C#**, which ranges from harmless
(caught and logged) to fatal (`Application.Quit()` in a boot method).

`recon.py` prints the complete P/Invoke surface: assembly → module → function
names.  That list is the work plan.

## 1. Classify each imported module

| class | examples | what to do |
|---|---|---|
| Windows-only platform APIs | screen readers (`Tolk`, `nvdaControllerClient`, Chinese reader APIs), Win32 overlays, `kernel32`, `user32` | no-op shim; they are usually optional and the game has a "not available" path |
| cross-platform SDK with a macOS build | audio/rendering/DRM SDKs shipped as `.dll` for Windows (`Live2DCubismCore`, FMOD, Steam, Discord, Epic, sqlite…) | supply the vendor's macOS library under the name Mono looks for (often a `.bundle` that must be renamed/copied to `lib<name>.dylib`) |
| boot-gating SDK | Steamworks is the classic: `SteamAPI.RestartAppIfNecessary` → `Application.Quit()` when the library is absent | either a real macOS library, or a shim that satisfies the handshake (§3) |

`gen_plugin_stubs.py` generates the first and third kinds automatically:

```
gen_plugin_stubs.py --managed "<App>.app/Contents/Resources/Data/Managed" --out ./shims
# -> shims/lib<module>.c (+ .dylib when clang is available), shims/dllmap.conf, README.txt
```

Then copy the dylibs into `Contents/Frameworks/MonoEmbedRuntime/osx/` **and**
`Contents/Plugins/`, append `dllmap.conf` **inside** `<configuration>` of
`Contents/MonoBleedingEdge/etc/mono/config` (mirror the file to `etc/config`),
and `codesign --force --deep -s - <App>.app`.

Every generated stub appends its name to `/tmp/plugin_stub.log` (override with
`PLUGIN_STUB_LOG`).  That log is the cheapest way to see which part of an SDK a
game actually touches – e.g. it shows Steamworks walking its interface list.

## 2. No-op shims: `Tolk` as the model case

Tolk is a screen-reader bridge with a tiny C API (`Tolk_Load`, `Tolk_Output`,
`Tolk_DetectScreenReader`, …).  A shim that reports "no screen reader present" is
exactly the state the game already handles on a machine without assistive tech:

```c
void Tolk_Load(void) {}
const wchar16 *Tolk_DetectScreenReader(void) { return 0; }
int  Tolk_HasSpeech(void) { return 0; }        /* return int, not bool */
```

Two details that generalise:

* **Return `int` (0/1) for `bool` returns.**  Mono may marshal a managed `bool` as
  1 byte or 4; a 4-byte 0/1 read as a 1-byte bool still gives the right value,
  whereas a C `bool` in a 4-byte slot can leave garbage in the upper bytes.
* **`wchar_t` differs**: Windows is UTF-16, macOS/Linux UTF-32.  Mono marshals
  `LPWStr` as UTF-16, so declare parameters as `const uint16_t *`.  Returned
  strings must be UTF-16 too – a stub that returns a C literal will produce
  garbage text on macOS.

`scripts/tolk_stub.c` is a ready-made example.

## 3. Boot-gating SDKs: the Steamworks pattern

Many Steam games quit when Steam is unavailable.  The shape is:

```csharp
try { if (SteamAPI.RestartAppIfNecessary(new AppId_t(NNN))) { Application.Quit(); return; } }
catch (DllNotFoundException) { Application.Quit(); return; }        // library missing -> quit
m_bInitialized = SteamAPI.Init();
if (!m_bInitialized) Debug.LogError("[Steamworks.NET] SteamAPI_Init() failed…");
```

Symptom without any shim: the game starts, logs nothing useful, and exits within
seconds.  Confirm with lldb (breakpoint on `exit` / `abort`) and by decompiling
the managed assembly; then satisfy the handshake:

* `SteamInternal_SteamAPI_Init` must return **`0`** (`k_ESteamAPIInitResult_OK`);
  it is an enum, not a boolean – returning 1 means "FailedGeneric".
* `SteamInternal_CreateInterface`, `SteamAPI_GetHSteamUser`, `SteamAPI_GetHSteamPipe`,
  every `SteamAPI_ISteamClient_GetISteam*` **and** every
  `SteamInternal_FindOrCreate*Interface` must return a **non-NULL** handle:
  `Steamworks.NET`'s `CSteamAPIContext.Init()` null-checks ~30 interface pointers
  (including `SteamNetworkingUtils/Sockets/Messages` via `FindOrCreate*`), and one
  NULL makes `SteamAPI.Init()` return false.
* `SteamAPI_RestartAppIfNecessary` → `0` (do not relaunch through Steam).
* Everything else can return 0; the SDK's callback pump
  (`ManualDispatch_RunFrame` / `GetNextCallback`) then simply finds no callbacks.

With a real macOS `libsteam_api.dylib` (Steamworks SDK, or the copy inside any
macOS Steam game) and a logged-in Steam client, `steam_appid.txt` (the game's
AppID) in the app's Data directory is enough for `SteamAPI_Init()` to succeed
outside Steam; that restores achievements/cloud/workshop.

### Derived settings: the "SDK-reported language" trap

Games often persist a setting (language, region) through the SDK and read it back
at startup, *overwriting* the value they saved.  Example found in the wild:

```csharp
// startup
Data<GameConfigData>.Load();
Data<GameConfigData>.Instance.Language = SteamManager.GetSteamPlayerLanguage();  // overwrites the save
```

With a no-op Steam shim that returns an empty string, the player silently resets
the user's choice to the fallback every launch ("changed the language, restarted,
it reverted").  Fix by making the shim answer from the game's own settings file:
`SteamAPI_ISteamApps_GetCurrentGameLanguage` (and `…GetSteamUILanguage`) read the
JSON the game wrote and map its enum to a Steam language string
(`schinese`, `japanese`, `english`, `koreana`, …).  `gen_plugin_stubs.py`
implements exactly this with `--steam-lang-config/--steam-lang-field/--steam-lang-map`.

## 4. Finding boot gates in the managed code

Decompile the assemblies (Mono builds only) and read the boot path:

```
# install once:  brew install dotnet && dotnet tool install -g ilspycmd
export DOTNET_ROOT="$(brew --prefix dotnet)/libexec"      # otherwise the apphost cannot find the runtime
ilspycmd -o /tmp/decomp "<App>.app/Contents/Resources/Data/Managed/Assembly-CSharp.dll"
grep -rn "Application.Quit\|DllImport\|GetSteamPlayerLanguage" /tmp/decomp | head
```

Useful searches: `Application.Quit`, `DllNotFoundException`, unconditional
assignments of settings from an SDK getter, and `#if UNITY_STANDALONE_WIN`-style
platform branches (they are usually dead on macOS, but a `RuntimePlatform` check
can change behaviour).

## 5. IL2CPP builds

If `Data/Native/GameAssembly.dll` exists and `Data/Managed/` holds only stub
assemblies, the C# has been compiled to native code: the game logic and every
`DllImport` call site are Windows machine code and **no data swap can work**.
The only routes are the developer's macOS build, or running the Windows build
under a translation layer (Wine/CrossOver/Whisky).
