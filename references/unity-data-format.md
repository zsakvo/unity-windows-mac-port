# Unity player data: what is platform-specific and what is not

Applies to any Unity **Mono** standalone build (2019.x–2022.x).  Verified in
detail against 2020.3.x; always re-check the platform stamps with `recon.py`
instead of assuming a version.

## 1. What a Windows build contains

| part | platform-specific? |
|---|---|
| engine binary (`UnityPlayer.dll` / `.dylib` + launcher + mono runtime) | **yes** |
| managed assemblies (`*_Data/Managed/*.dll`) | no (IL, runs on any mono) |
| serialized assets (`globalgamemanagers`, `level0`, `*.assets`, `*.resS`) | mostly no |
| compiled shader sub-programs (`Shader.platforms`) | **yes** |
| native plugins (`Plugins/x86_64/*.dll`) | **yes** |
| Addressables bundles (`StreamingAssets/aa/<BuildTarget>/…`) | **yes** (bundle header) |

So a port = graft the game's data onto a macOS player of the **same Unity
version**, then fix the three platform-stamped things.  `make_app.sh` grafts,
`patch_game_data.py` fixes, `gen_plugin_stubs.py` supplies native stand-ins.

Stop conditions worth checking first (see `other-engines.md`): if the build is
**IL2CPP** (`Data/Native/GameAssembly.dll`, no `Managed/*.dll`) the managed code
is native Windows machine code and no data swap can work.

## 2. Version matching is strict, including edition suffixes

Every serialized file starts with a `SerializedFile` header (big-endian fields
even on little-endian platforms):

```
uint32 metadata_size
int64  file_size
uint32 version          (22 for 2020.3, 21 for 2019, ...)
uint32 data_offset
byte   endianness       0 = little
byte[3] reserved
string unityVersion     int32 length + bytes, 4-byte aligned
int32  targetPlatform   19 = StandaloneWindows64, 2/16 = macOS variants, ...
bool   enableTypeTree
```

The player compares its own version string with that field and refuses a
mismatch:

```
Invalid serialized file version. File: ".../globalgamemanagers".
Expected version: 2020.3.24f1. Actual version: 2020.3.24f1c2.
-> Failed to initialize engine
```

Edition suffixes matter: a `…f1c2` (Unity China) build and a `…f1` build are
*different* versions.  Use the player module from the same changeset as the game
(the changeset is in the version string inside the game's `UnityPlayer.dll`,
e.g. `2020.3.24f1c2_f3616f7ef48a`) and build the shader donor with that same
editor version.

## 3. The graphics API list is data, not binary

Unity's *PlaybackEngine* player templates carry no baked graphics-API mask; the
player reads it from `globalgamemanagers`:

* `BuildSettings.m_GraphicsAPIs` — a vector of `GraphicsDeviceType` ints, the
  **last field** of the BuildSettings object.  Windows builds carry `[2]`
  (Direct3D11); a macOS build carries `[16]`.
* `GraphicsSettings.m_ShaderDefinesPerShaderCompiler[i].shaderPlatform` — the
  shader compiler platform: `4` = D3D11, `14` = Metal, `15` = OpenGLCore.

With a Windows stamp on macOS the player creates a **NullGfxDevice** and crashes
while loading the error shader:

```
GfxDevice: creating device client; threaded=1
NullGfxDevice:
    Version:  NULL 1.0 [1.0]
The resource Internal-ErrorShader.shader could not be loaded from the resource file!
... crash in Material::BuildProperties
```

There is an escape hatch (`-force-metal -force-gfx-without-build`) but it logs
`Forced GfxDevice 'Metal' was not built from editor, shaders will not be available`
and the engine then refuses **every** shader – a dead end.  Patch the stamp.

Both values are single little-endian int32s: patch them as **raw bytes**.
`patch_game_data.py` finds them by shape (a `count=1, platform` vector inside the
GraphicsSettings object; the trailing int32 of BuildSettings) rather than by
remembered offsets, because offsets move whenever a file is rewritten.

## 4. UnityPy caveats (the write path)

* `globalgamemanagers` from a **Unity China** build has a `PlayerSettings` layout
  UnityPy's class database cannot parse (`read_i_array out of bounds`).  Never
  rewrite that file through UnityPy; raw-patch its two stamps instead.
* `SerializedFile.save(packer=None)` rewrites the whole file and **moves object
  offsets**.  Order matters: do raw byte patches first, then UnityPy typetree
  writes; never compute an offset, rewrite, then reuse the old offset.
* Write path: `ObjectReader.save_typetree(tree)` on each changed object, then
  `env.file.save()` → bytes.
* Side-car streams (`*.resS`, `*.resource`) stay untouched, so keep them beside
  the file.
* Type trees are usually stripped in players (`enableTypeTree=false`), so UnityPy
  relies on its bundled class database – another reason to match versions.

## 5. Where a macOS player looks for things

```
<App>.app/Contents/
├── Info.plist                     CFBundleExecutable must match MacOS/<name>
├── MacOS/<name>                   launcher (PlayerMain -> NSApplicationMain)
├── Frameworks/
│   ├── UnityPlayer.dylib          engine
│   ├── libmonobdwgc-2.0.dylib     mono runtime
│   ├── libMonoPosixHelper.dylib
│   └── MonoEmbedRuntime/osx/      Mono's DllImport search directory
├── Plugins/                       Unity plugin dir (case-insensitive == PlugIns)
├── MonoBleedingEdge/etc/mono/config   mono config; dllmap entries live here
└── Resources/
    ├── unity default resources    engine built-ins, macOS-compiled
    ├── unity_builtin_extra        project built-ins (mirror of Data/Resources/…)
    ├── MainMenu.nib
    └── Data/                      the original <Game>_Data contents
```

* The player accepts `Contents/Resources/Data` or `Contents/Data`.
* It logs to `~/Library/Logs/<company>/<product>/Player.log`; company/product come
  from `Data/app.info`.  `Application.persistentDataPath` is
  `~/Library/Application Support/<bundle-id>/`; PlayerPrefs end up in
  `~/Library/Preferences/<bundle-id>.plist`.
* Built-in resources are loaded from **two** places: the engine's own
  `Contents/Resources/unity default resources` and the project's
  `Data/Resources/unity_builtin_extra`.  The path ids of Unity's built-ins are
  identical on all platforms, so the macOS module's `unity default resources`
  (same object ids, Metal/GL blobs) can be dropped in directly.

## 6. Getting a macOS player with no Unity licence

`UnitySetup-Mac-Mono-Support-for-Editor-<version>.pkg` contains ready-made player
bundles:

```
https://download.unity3d.com/download_unity/<changeset>/MacEditorTargetInstaller/UnitySetup-Mac-Mono-Support-for-Editor-<version>.pkg
https://download.unitychina.cn/download_unity/<changeset>/MacEditorTargetInstaller/UnitySetup-Mac-Mono-Support-for-Editor-<version>.pkg   (China)

xar -xf <pkg>                       # -> TargetSupport.pkg.tmp/{Bom,Payload,PackageInfo}
gzip -dc TargetSupport.pkg.tmp/Payload | cpio -idm --quiet
# -> Variations/macos_{arm64,x64,x64arm64}_{development,nondevelopment}_mono/UnityPlayer.app
#    MonoBleedingEdge/etc/...            mono config tree
#    Source/Player/MacPlayer/MacPlayerEntryPoint/Resources/MainMenu.nib
```

`macos_x64arm64_*` is universal (native arm64 + x86_64); `macos_arm64_*` is
arm64-only.  The companion editor package
(`MacEditorInstaller/Unity.pkg`) is only needed if you must *build* shaders (see
`shader-rebake.md`), and that build does require an activated Unity licence.
