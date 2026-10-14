# Re-baking shaders for macOS

The only hard blocker in a Unity Windows→macOS data swap: a Windows build ships
**D3D11-only** shader sub-programs and macOS needs **Metal**.  D3D11 DXBC
bytecode cannot be converted back into the Metal *source* Unity expects, so the
shaders have to be compiled again – by a Unity editor, for macOS.

## 1. Diagnose first

`recon.py` lists every `Shader` object with its `platforms` array
(`4` = D3D11, `14` = Metal, `15` = OpenGLCore) and the `(shader, keywords)` pairs
the game's `Material` objects actually use.  Typical findings:

* every project/package shader: `platforms=[4]` → needs re-baking;
* Unity built-ins are already broken out per platform (the module's
  `unity default resources` is macOS-compiled).

At runtime the failure reads:

```
Desired shader compiler platform 14 is not available in shader blob
ERROR: Shader <name> shader is not supported on this GPU (none of subshaders/fallbacks are suitable)
```

## 2. Which shaders must be baked

Only the ones the game actually uses.  `recon.py` prints the material usage; the
donor project must contain a shader of the **same name** (and the same keyword
set) for each of them, plus whatever the game loads dynamically by name.

Practical split:

| kind | where the source comes from |
|---|---|
| Unity built-ins (`Sprites/Default`, `UI/Default`, `Sprites/Mask`, `Hidden/*`) | automatic: any macOS player build bakes them, with stable path ids |
| package shaders (TextMeshPro, soft-mask, UI effects, Live2D, DOTween…) | the package/SDK source (install it into the donor project) |
| private/project shaders | usually unrecoverable: the player data keeps only the parsed form.  Re-implement from the parsed form (property names, subshader tags, pass count, keywords) if the effect matters, otherwise leave it |
| standard shaders from a newer Unity | copy the `.shader` source from an editor install of that version |

Recovering structure from a player build (UnityPy):

```
d = shader.read_typetree()
d["m_ParsedForm"]["m_PropInfo"]["m_Props"]        # property names/defaults
d["m_ParsedForm"]["m_SubShaders"][i]["m_Passes"]  # pass count, render state
d["m_ParsedForm"]["m_FallbackName"]               # fallback
```
The HLSL body is **not** in a player build, so a private shader can only be
approximated.  Record that in the port notes.

## 3. Build the donor (throw-away Unity project)

```
# needed-shaders.txt: one "<Shader Name>\t<keywords>" per line (recon.py output)
build_shader_donor.sh --unity <Unity.app/Contents/MacOS/Unity> \
                      --needed needed-shaders.txt \
                      --shaders-src ./shader-sources
```

What the script does and why:

1. creates a minimal project (`ProjectSettings/ProjectVersion.txt` with the
   **same version as the game**, empty `Packages/manifest.json`),
2. copies the shader sources into `Assets/`, rewriting `#pragma shader_feature*`
   to `#pragma multi_compile*` so **all** variants get baked rather than only the
   ones the donor project happens to use,
3. drops in `BuildDonor.cs`, which creates one material per
   `(shader, keywords)` pair under `Assets/Resources/Donor/`, adds every shader to
   *Always Included Shaders*, and builds `donor-build/Donor.app` for macOS,
4. prints the donor's data files.

Gotchas:

* **Do not use `-batchmode`.**  Batch mode takes Unity's headless entitlement
  path, which on Personal/Pro seats fails the licence handshake
  (`Failed to activate/update license …`), while the GUI path with `-executeMethod … -quit`
  succeeds.  Same for the auto-shader-compile step: run the editor normally.
* The donor project needs no artwork, scenes or scripts – an empty scene plus
  materials in `Resources` is enough.
* If a package source is missing (e.g. a proprietary SDK), reconstruct that one
  shader from its parsed form; only its name, properties and keywords have to
  match for the game's materials to bind.
* Keep the donor app around: it is the shader source of truth for the rest of the
  port and for future game updates.

## 4. Transplant the blobs

`patch_game_data.py --data <App>/Contents/Resources/Data --donor donor-build/Donor.app`
walks every serialized data file and calls
`transplant_shaders.py --whole --clear-fallback` per file.

Details that matter:

* **Match by path id first** (Unity built-ins: `Sprites/Default` = 10753,
  `UI/Default` = 10770, …), then **by shader name** (package shaders get
  per-project ids).
* **Replace the whole shader object**, not just `platforms/offsets/…/compressedBlob`.
  Copying only the blob leaves the target's parsed form paired with a foreign blob
  and the result is still "shader is not supported on this GPU".
* **Blank `m_ParsedForm.m_FallbackName`.**  Resolving a fallback during shader
  creation re-enters `Shader::CreateFromParsedForm` and crashes 2020.3 here:

  ```
  #0 DeferredShaderActions::ApplyDeferredShaderLoadActions()
  #1 Shader::CreateFromParsedForm()
  #5 ShaderLab::IntShader::ResolveFallback(...)
  #16 PlayerLoadGlobalManagers(...)
  ```
  Once every shader has a working program the fallback is unnecessary.
* Donor files can be big; skipping files > ~256 MB keeps UnityPy's memory sane.
  Shader objects normally live in `resources.assets`, `globalgamemanagers.assets`,
  `sharedassets*.assets` and `Resources/unity_builtin_extra`.
* Verify afterwards by re-reading `platforms` (should contain 14) and by running
  `verify_port.sh` (expect `ERROR: Shader` count 0).

## 5. If some shaders stay broken

* A shader that no material uses (and that is not force-included) can stay
  D3D11-only; it will only matter if something calls `Shader.Find` for it.
* "shader is not supported" that survives a whole-object transplant usually means
  the donor was built by a **different Unity version** than the player.
* Missing variants at runtime show up as fallbacks/artifacts rather than errors:
  bake more variants by converting `shader_feature` to `multi_compile` (the
  donor script already does) or by adding materials with those keywords.
* Bundles (Addressables) carry their own shaders and their own
  `targetPlatform`; they need the same treatment and a platform-field patch.
