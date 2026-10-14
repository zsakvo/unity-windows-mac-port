# macOS host pitfalls and the diagnostic toolkit

## 1. Do not run the port from `~/Documents`, `~/Desktop`, `~/Downloads`

macOS TCC protects those folders.  An app that lives inside one of them can be
blocked from reading *its own bundle*, and the failure mode is nasty: the process
blocks inside `open()` and looks hung, and repeated attempts can wedge IO for the
whole machine.

Observed signature (this cost a long debugging session):

```
sample <pid>            # main thread leaf:
  ... -> PlayerLoadSceneFromThread -> CompleteAwakeSequence
      -> MonoBehaviour::CallAwake -> scripting_method_invoke
      -> mono_marshal_get_native_wrapper -> mono_lookup_pinvoke_call
      -> FindAndLoadUnityPlugin -> LoadPluginExecutable -> dlopen -> open   <-- blocked forever
```

The same build run from `/tmp` or `~/Applications` works.  So:

* ship/install into `~/Applications` or `/Applications`;
* `verify_port.sh` warns if the app is inside a protected folder;
* if a newly assembled app "hangs at startup", test it from `/tmp` before
  debugging anything else.

## 2. Signing

* arm64 Mach-O binaries must be signed to execute at all, and re-signing is
  required after any change to a bundle's `Info.plist` or nested code:
  `codesign --force --deep -s - "<App>.app"`.
* Sign nested dylibs first, then the bundle.  `codesign --verify --verbose=1`
  should say "satisfies its Designated Requirement".
* `codesign --deep` fails with
  `bundle format unrecognized, invalid, or unsuitable … in subcomponent …/etc/mono/4.0`
  when the mono config tree (`MonoBleedingEdge/etc/mono/{2.0,4.0,4.5}`) sits under
  `Contents/Frameworks/`: those look like versioned bundle directories.  Keep
  `MonoBleedingEdge` directly under `Contents/` (which is also where the player
  reads its config from) and the problem disappears.
* Locally built bundles carry no quarantine attribute; anything downloaded does
  (`xattr -dr com.apple.quarantine "<App>.app"`).

## 3. Diagnostics, cheapest first

| tool | use |
|---|---|
| `~/Library/Logs/<company>/<product>/Player.log` | Unity's own log (stdout+logFile); the first place to look |
| `~/Library/Logs/DiagnosticReports/*.ips` | system crash reports — **accurately symbolized**.  Parse as JSON (skip the first line) and read `threads[].frames[]` of the triggered thread against `usedImages[]` |
| `sample <pid> 3 -file out.txt` | where a *hang* is: leaf frames include syscalls (`open`, `semaphore_wait_trap`, `psynch_cvwait`) |
| `lldb -p <pid> -b -o 'bt 30' -o detach -o quit` | quick attach; symbols for stripped binaries are unreliable, but frame *counts/order* still help |
| `lldb -b -w -n <ProcName> -o 'br set -n exit' -o 'br set -n abort' -o continue -o 'bt 20' -o quit` | catch **who quits**: shows the exact call site that called `exit()` |
| `screencapture -x out.png` | visual proof; needs Screen Recording permission for the calling application |
| `codesign -dv` / `--verify` | signature state |
| `spctl --assess` | Gatekeeper (only relevant for quarantined bundles) |

Unity's own crash handler catches SIGSEGV and writes a pseudo-backtrace into
`Player.log`.  Its symbol names are frequently wrong (`MetalHeap::AliasResources
+141592` style) — trust the `.ips` report instead.

## 4. Launch it the way a user would

* Use `open -n "<App>.app"` (LaunchServices).  Running `Contents/MacOS/<binary>`
  directly can fail to obtain a Metal drawable and crash inside
  `metal::SetAsDefaultFBO(color=0x0)` — an artefact, not a port bug.
* A process launched from a sandboxed shell (CI runners, agent sandboxes) inherits
  that sandbox; `open` hands off to launchd and escapes it.  If a log starts with
  `sandbox_extension_issue_file_to_process failed for <path>`, the app is running
  sandboxed and its file access (including its own `Data/`) may be restricted.
* Do **not** write `pkill -f "<pattern>"` when the pattern also appears in your own
  command line – it matches the shell running it and kills it.  Use
  `for p in $(pgrep -f "<pattern>"); do kill $p; done`.
* A "second copy" of a game can react oddly (duplicate-instance checks): make sure
  only one instance is running before judging startup behaviour.

## 5. State you can inspect or reset while testing

```
~/Library/Logs/<company>/<product>/Player.log     the log
~/Library/Application Support/<bundle-id>/        Application.persistentDataPath
                                                  (settings/save JSON files live here)
~/Library/Preferences/<bundle-id>.plist           PlayerPrefs (screen mode etc.)
```

Deleting the persistent-data directory is the cleanest "first run" reset, and
hand-editing its JSON is often the fastest way to test a hypothesis (language,
resolution, skip-intro flags) without clicking through menus.
