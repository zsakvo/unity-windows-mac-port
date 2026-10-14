# unity-windows-mac-port

将 Windows Unity Mono 游戏的构建数据接入同版本的 macOS Unity Player，并修复平台标记、Metal Shader 和原生插件兼容性，生成可在 macOS 原生运行的 `.app`。

这是一个供编码代理使用的技能包，也包含可手动运行的辅助脚本。完整工作指引见 [SKILL.md](SKILL.md)。移植过程需要逐个游戏分析，不是通用的一键转换器。

## 适用范围

- **主要支持 Unity Mono 构建**：保留托管程序集与资源，替换平台相关运行时。
- **不适用于直接转换 Unity IL2CPP 构建**：Windows 原生游戏逻辑无法通过替换 Player 运行。
- Godot、GameMaker、Ren'Py 等引擎的类似思路见 [其他引擎说明](references/other-engines.md)，本目录脚本主要面向 Unity。

目标是使用 macOS Player 和 Metal 原生运行，无需 Wine。最终兼容性取决于游戏所用的 Shader、原生 SDK、资源格式及平台专属代码。

## 环境准备

需要以下资源：

1. macOS 和 Python 3。
2. 完整、未修改的 Windows 游戏目录，通常包含 `Game.exe`、`UnityPlayer.dll` 和 `Game_Data/`。
3. 与游戏 **完全一致版本**的 Unity **Mac Build Support (Mono)** 模块，包括版本的地区或发行后缀。
4. 若需要重新编译 Shader：同版本 Unity Editor，以及已激活的 Unity 许可证。
5. 若需要编译插件替代库：提供 `clang` 的 Xcode Command Line Tools。

在技能目录中准备 Python 环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install UnityPy dnfile
```

始终在独立工作目录中处理副本，保留原始游戏安装。以下命令从本技能目录执行，路径和游戏名称需替换为实际值。

## 操作流程

### 1. 检查游戏构建

```bash
mkdir -p work
python3 scripts/recon.py "/path/to/GameDir" \
  --json work/recon.json \
  --needed-shaders work/needed-shaders.txt
```

报告用于确认：

- 游戏采用 Mono 还是 IL2CPP。
- Unity 版本及可提取的版本信息。
- 图形 API 和 Shader 平台标记。
- 材质使用的 Shader 与关键字组合。
- 托管程序集的 P/Invoke 导入，以及待处理的原生插件。
- Addressables 路径、公司与产品信息、Steam AppID 等线索。

若为 IL2CPP，停止本流程。若 Shader 只有 D3D11 编译数据，需要执行第 4 步。

### 2. 获取 Player 并组装应用

从 Unity 对应版本的 Mac Build Support (Mono) 安装包中取得 `MacStandaloneSupport`。获取方式、安装包解包和目录布局见 [Unity 数据格式说明](references/unity-data-format.md)。

```bash
bash scripts/make_app.sh \
  --game "/path/to/GameDir" \
  --player "/path/to/MacStandaloneSupport" \
  --app-name "My Game" \
  --bundle-id com.example.mygame \
  --out "$HOME/Applications" \
  --arch x64arm64
```

`--player` 可指向支持模块目录、`Variations` 目录或具体 Player 变体目录。默认架构为 `x64arm64`；可用 `--arch arm64` 选择 Apple Silicon 变体，前提是模块提供该变体。需要时可传入 `--steam-appid`。

脚本复制 Player 骨架和游戏数据，生成 `Info.plist`，并整理内置资源及 Mono 配置目录。示例后续使用的应用路径为 `$HOME/Applications/My Game.app`。

### 3. 修复平台标记

```bash
python3 scripts/patch_game_data.py \
  --data "$HOME/Applications/My Game.app/Contents/Resources/Data" \
  --stamps-only
```

该步骤修复 `BuildSettings.m_GraphicsAPIs` 和 `GraphicsSettings` 中的平台标记，使 Player 能选择 macOS 图形设备。它不会为 Windows Shader 自动生成 Metal 编译数据。

### 4. 构建并移植 Metal Shader

准备 `work/shader-sources/`，放入游戏所需的包、SDK 或自定义 Shader 源码。Unity 内置 Shader 由 donor 工程提供。无法取得的私有 Shader 需要按解析信息重建，并记录视觉差异。

```bash
bash scripts/build_shader_donor.sh \
  --unity "/path/to/Unity.app/Contents/MacOS/Unity" \
  --version "<游戏的完整 Unity 版本>" \
  --needed work/needed-shaders.txt \
  --shaders-src work/shader-sources

python3 scripts/patch_game_data.py \
  --data "$HOME/Applications/My Game.app/Contents/Resources/Data" \
  --donor scripts/donor-build/Donor.app
```

默认 donor 工程位于 `scripts/shader-project/`，构建产物位于 `scripts/donor-build/Donor.app`，日志位于 `scripts/donor-build.log`。示例显式传入版本，避免版本检测失败时使用脚本的默认值。

构建脚本使用 GUI 模式启动 Editor，并通过 `-executeMethod` 和 `-quit` 完成任务。按本技能的工作流程，不添加 `-batchmode`，以避免相关许可证握手问题。

移植默认替换整个 Shader 对象并清理 fallback。只替换编译 blob 可能导致解析结构不匹配。机制和重建建议见 [Shader 重编译说明](references/shader-rebake.md)。

### 5. 处理原生插件

```bash
python3 scripts/gen_plugin_stubs.py \
  --managed "$HOME/Applications/My Game.app/Contents/Resources/Data/Managed" \
  --out work/shims
```

脚本输出 C 源码、`dllmap.conf` 和安装说明；存在 `clang` 时尝试编译通用架构 `.dylib`。根据 `work/shims/README.txt` 将替代库放入应用的插件与 Mono 搜索目录，将映射条目合并到 `Contents/MonoBleedingEdge/etc/mono/config` 的 `<configuration>` 内。

需要逐个检查导入模块：可移植 SDK 优先使用其 macOS 实现；空实现只适用于游戏能够接受功能缺失的模块。自动生成的占位库不能保证满足实际调用约定、返回值或 SDK 行为。

Steam 相关替代实现提供部分启动握手和语言读取逻辑。若游戏启动时从 SDK 覆盖语言设置，可通过 `--steam-lang-config`、`--steam-lang-field` 和 `--steam-lang-map` 指向实际配置。详细说明见 [原生插件处理](references/native-plugins.md)。

完成修改后进行本地临时签名：

```bash
codesign --force --deep -s - "$HOME/Applications/My Game.app"
```

### 6. 启动验证

```bash
bash scripts/verify_port.sh "$HOME/Applications/My Game.app" \
  --seconds 40 \
  --screenshot work/shot.png
```

脚本会启动应用，检查 Player 日志中的图形设备、Shader、插件和序列化版本错误，以及进程是否提前退出。默认观察 40 秒。它会清理已有 Player 日志以检查本次启动，因此需要保留旧日志时应先备份。

启动检查通过后，还需手动验证主菜单、场景切换、画面、声音、输入和存档。短时间进程存活不能证明游戏全部功能正常。

本技能建议将应用放在 `~/Applications` 或 `/Applications`，以减少受保护目录引发的访问问题。日志与 macOS 诊断方法见 [宿主环境说明](references/macos-host.md)。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `SKILL.md` | 代理使用的完整流程与决策指引 |
| `scripts/recon.py` | 分析构建、Shader、材质及原生导入 |
| `scripts/make_app.sh` | 组装 macOS 应用包 |
| `scripts/patch_game_data.py` | 修复平台标记并调用 Shader 移植 |
| `scripts/build_shader_donor.sh` | 创建并构建 Shader donor 工程 |
| `scripts/BuildDonor.cs` | Unity Editor 构建入口 |
| `scripts/transplant_shaders.py` | 按对象 ID 或名称移植 Shader |
| `scripts/gen_plugin_stubs.py` | 生成原生插件占位库和 Mono 映射 |
| `scripts/tolk_stub.c` | Tolk Windows 屏幕阅读器桥接的空实现 |
| `scripts/verify_port.sh` | 启动应用并检查已知故障 |
| `references/` | 数据格式、Shader、插件与宿主诊断参考 |

## 常见问题

| 表现 | 优先检查 |
| --- | --- |
| `NullGfxDevice` 或初始化材质时崩溃 | 第 3 步的平台标记是否完成 |
| `shader is not supported on this GPU` | 是否具有 Metal 数据、是否替换整个对象、donor 版本是否匹配 |
| `ResolveFallback` 附近崩溃 | 移植时是否清理 Shader fallback |
| `Internal-ErrorShader.shader could not be loaded` | 内置资源是否位于正确的 `Contents/Resources/` 布局 |
| `DllNotFoundException` | macOS 插件、Mono 搜索路径和 `dllmap` 配置 |
| 启动后很快退出 | 原生 SDK 初始化失败或游戏主动退出逻辑 |
| `Invalid serialized file version` | Player 与数据的 Unity 版本及发行后缀是否一致 |
| Editor 许可证激活失败 | 是否使用已激活的同版本 Editor，以及 GUI 构建方式 |
| 设置重启后恢复默认 | 游戏是否从 SDK 返回值覆盖本地配置 |
| 启动无日志或卡住 | 应用位置、TCC 权限及宿主诊断 |

## 使用边界

按技能约定，仅处理自己拥有的游戏供个人使用，不分发包含游戏资源或专有组件的组装应用。保留商店的授权与 DRM 行为；插件兼容处理不能替代游戏授权。更多边界说明见 [其他引擎与使用范围](references/other-engines.md)。
