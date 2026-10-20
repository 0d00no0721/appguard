# AppGuard 打包与安装包（0.3.0）

> 状态：**源码版 + 冻结版 + Inno Setup 安装包已构建**。
> 自动测试覆盖了源码/冻结 exe 的 daemon/web/CLI/托盘启动；真正安装需要用户点一次 UAC，
> 我没有在自动化里替你安装（安装器会创建最高权限登录任务）。

## 0. 现在已有的封装形态

- 用户入口：`AppGuard.bat`（唯一根目录 `.bat`）
- 实现：`tools/appguard_launcher.py` + `tools/win_tray.py`（ctypes 自绘托盘，不依赖 pystray/Pillow）
- 行为：
  - 启动/复用守护与 Web 网关，打开 Edge `--app` 窗口；
  - 关闭 Edge 窗口时，按 `%LOCALAPPDATA%\AppGuard\launcher.json` 的 `close_action`
    选择“最小化到托盘”或“退出并停止 AppGuard”；
  - 托盘菜单：打开界面 / 关闭窗口行为 / 以管理员安装或重启 / 创建快捷方式 /
    打开日志 / 退出（保留后台或停止服务）；
  - 命名互斥量单实例；第二次双击只打开/聚焦窗口；
  - 子进程统一用 `console_python()`（pythonw 下 `sys.stdout=None`，不能直接 spawn pythonw 跑 CLI）。
- 内部脚本已收进 `tools/`：`AppGuard-admin.bat`、`install.bat`、`uninstall.bat`、
  `make_shortcut.bat`。根目录只保留 `AppGuard.bat`。

## 1. 目标产物

```text
E:\小工具\app_guard\dist\installer\AppGuardSetup-0.3.0-x64.exe
E:\小工具\app_guard\dist\installer\AppGuardSetup-0.3.0-x64.exe.sha256
```

安装后：

```text
<用户选择目录>\AppGuard\            # 默认 C:\Program Files\AppGuard，可改 D:\Apps\AppGuard
├── AppGuard.exe                    # 单 exe 多模式；默认 = 托盘启动器
├── _internal\...                   # PyInstaller onedir 运行时 + _internal\web
├── README-zh.md
└── THIRD_PARTY_NOTICES.txt

C:\ProgramData\AppGuard\            # 数据固定；config/audit/kb/daemon.pid
%LOCALAPPDATA%\AppGuard\            # web-endpoint.json / launcher.log / launcher.json
```

**安装位置可自定义**：Inno Setup 的 `DisableDirPage=no`，所有路径使用 `{app}`，
计划任务/快捷方式/卸载都跟 `{app}` 走；程序在 D 盘、数据仍在 ProgramData（machine-wide），
v1 不做“数据目录自定义”。

## 2. 单 exe 多模式（PyInstaller onedir）

```text
AppGuard.exe                       # 默认：托盘启动器
AppGuard.exe daemon [--transport auto]
AppGuard.exe web --port 0 --endpoint ...
AppGuard.exe cli status|stop|doctor|...
```

- `--onedir`，不要 onefile（启动快、杀软误报少）；
- `--windowed`，daemon/web 日志写文件，不依赖 stdout；
- `--add-data "web;web"`；`--icon appguard.ico`；
- 显式 hidden imports：`win32com.client`、`win32timezone`、`win32file`、`win32pipe`、
  `win32security`、`pythoncom`、`win_tray`；
- 排除 `tkinter`、`tests`、`ui`、`archive`；
- 不用 UPX。

### frozen 适配（必须在打包前改）

| 位置 | 现在（源码） | frozen 后 |
|---|---|---|
| `appguard_web.guard_argv` | `[console_python(), "tools/guardctl.py", ...]` | `[sys.executable, "daemon", ...]` 或 `[sys.executable, "web", ...]` |
| `appguard_launcher.stop_app` | `python tools/guardctl.py stop` | `[sys.executable, "cli", "stop"]` |
| `appguard_launcher.request_admin` | `python tools/elevate_install.py` | `AppGuard.exe admin-install`（内部 UAC 自提权） |
| `appguard_launcher.MAKE_SHORTCUT` | `python tools/make_shortcut.py` | `AppGuard.exe shortcut` |
| `appguard_web` 静态目录 | `ROOT/web` | `os.path.dirname(sys.executable)/web`（onedir） |
| `make_shortcut.py` | pythonw + script | `AppGuard.exe` + 图标 |
| 版本 | `src/appguard/__init__` 0.1.0 / `web.py` 0.3.0 / 页面 v0.3 | 统一 `version.py`，exe 资源/安装器/页面同源 |

> 这些适配只影响“打包版”；源码版继续用 `tools/*.py` 和 `console_python()`。

## 3. Inno Setup 6 安装器

```ini
[Setup]
AppId={{...固定 GUID...}}
AppName=AppGuard
AppVersion=0.3.0
DefaultDirName={autopf}\AppGuard
DisableDirPage=no
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=AppGuardSetup-0.3.0-x64
```

安装流程：

1. 停旧版本：`AppGuard.exe cli stop`，等待 pipe/`daemon.pid` 释放；
2. 删除/替换 `{app}` 文件；**保留** `C:\ProgramData\AppGuard\config.toml`；
3. 仅当 config 不存在时写默认模板（`dry_run=true`）；
4. 注册当前用户登录任务：
   - 名称 `AppGuard`，`onlogon`，`/rl highest`；
   - 操作：`"{app}\AppGuard.exe" daemon --transport auto`；
   - 立即 `schtasks /run`，轮询 `status.get.elevated=true`；
5. 创建开始菜单/桌面快捷方式，指向 `{app}\AppGuard.exe`，图标 `{app}\appguard.ico`；
6. 完成页可勾选“启动 AppGuard”。

卸载：

- 停守护（`AppGuard.exe cli stop`）、删任务、停网关（端点 pid）；
- 删 `{app}`、快捷方式；
- 默认保留 `C:\ProgramData\AppGuard`，提供“同时删除数据”选项；
- 删除 `%LOCALAPPDATA%\AppGuard` 端点/日志/launcher.json。

升级：

- 固定 `AppId`；停旧任务/守护 → 保留数据 → 替换 `{app}` → 重启任务；
- 验证 `elevated=true`、`frontend=ready`、`dry_run` 保持用户设置。

## 4. 安装目录校验（Inno `[Code]`）

- 拒绝 UNC/网络路径；
- 只允许本地固定盘（`GetDriveType == DRIVE_FIXED`）；
- 路径含空格/中文允许，任务参数正确加引号；
- 检查剩余空间；
- 装在非系统盘时提示“该盘必须在登录时可用”。

## 5. 构建流水线（后续）

```text
packaging/
├── entry.py                 # 多模式分发：launcher/daemon/web/cli/admin-install/shortcut
├── AppGuard.spec            # PyInstaller onedir
├── AppGuard.iss             # Inno Setup
├── version_info.txt
├── config.template.toml
├── THIRD_PARTY_NOTICES.txt
└── build_release.ps1
```

顺序：

1. `python tests\run_all.py`（255 项）；
2. `pyinstaller packaging\AppGuard.spec --noconfirm --clean`；
3. 冒烟：`AppGuard.exe daemon`（临时 data-dir）、`AppGuard.exe web` + health、
   `AppGuard.exe` 托盘；
4. `iscc packaging\AppGuard.iss`；
5. 签名（强烈建议 Authenticode；不签会有 SmartScreen/杀软提示）；
6. 生成 `.sha256`；
7. 干净 Win11 VM 验收。

## 6. 验收矩阵（安装包阶段）

| 场景 | 通过标准 |
|---|---|
| 干净 Win11 x64，不装 Python | 安装、托盘、UI 正常 |
| 管理员安装 | 任务创建，`elevated=true` |
| 自定义目录 D:\Apps\AppGuard | 任务/快捷方式/卸载都指向 `{app}` |
| 普通用户双击 | 不要求 UAC；只读/演练可用 |
| 关闭 Edge 窗口 | 按设置最小化到托盘或退出并停服务 |
| 托盘“以管理员重启” | UAC 后 elevated=true，UI 自动刷新 |
| `dry_run=true` | 只写审计 |
| 关闭 dry_run | 真实清理，审计 `dry_run=false` |
| 登录/重启 | 管理员守护自启 |
| 升级 | 保留 config/audit/kb 与安装位置 |
| 卸载 | 停守护、删任务；数据按选择保留 |
| 中文用户名/路径 | 端点、日志、任务路径正常 |

## 7. 已构建的 0.3.0 产物与测试

```text
dist\AppGuard\AppGuard.exe                          # 便携/冻结版（onedir）
dist\installer\AppGuardSetup-0.3.0-x64.exe          # Inno Setup 安装包
dist\installer\AppGuardSetup-0.3.0-x64.exe.sha256
```

当前 SHA256：

```text
ff861698aab6c3ea54c89fe0c96548bc1d4843e36c050664ed547b7891940ed3
```

构建命令：

```powershell
# 完整构建（测试 + PyInstaller + Inno + SHA256）
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1

# 只重打冻结版
D:\python\python.exe -m PyInstaller packaging\AppGuard.spec --noconfirm --clean

# 只重打安装包（先有 dist\AppGuard）
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\AppGuard.iss
```

已自动化验证：

- `tests\run_all.py`：255/255；
- `AppGuard.exe daemon`（自定义 data-dir/pipe）启动、`AppGuard.exe web` 返回 health=ok、
  `/`/`app.css`/`app.js` 200；
- `AppGuard.exe cli init` 可创建数据目录；
- 源码版托盘：启动、关闭窗口 → 托盘/quit 两种行为、单实例；
- 冻结版托盘：自定义托盘进程启动、服务就绪；**测试环境里的 Edge 实例处于异常状态**
  （已弹出“现有实例正在以提升的权限运行”），所以冻结版自动开 Edge 窗口未完成验收；
  需先彻底重启 Edge 后再测；
- 安装包由 Inno Setup 6.7.3 编译成功；
- **未自动执行**：安装器的 UAC 安装/卸载、Windows 干净机验收、实盘清理。
- 启动器不再使用 pystray/Pillow；冻结包不包含它们。

## 8. 安装/使用（交给用户）

1. 双击 `dist\installer\AppGuardSetup-0.3.0-x64.exe`；
2. UAC 选“是”；
3. 目录页可改到 `D:\Apps\AppGuard` 等；数据仍固定在 `C:\ProgramData\AppGuard`；
4. 安装完成后勾选“Launch AppGuard”（以**原普通用户**启动，安装器已用
   `runasoriginaluser`，不会把 Edge 提权）；
5. 首次打开若显示“只读/演练”，检查 `AppGuard` 登录任务；
6. 设置 → 关闭 dry_run 才能实盘清理。

**注意**：托盘必须普通用户运行；只有安装器和登录任务是管理员。
如果 Edge 报“现有实例以提升权限运行”，说明之前有一个被提权的 Edge 实例；先彻底退出
Edge（或重启 Edge）再启动 AppGuard。新版安装器的“Launch AppGuard”已按原用户启动，
不会主动提权 Edge。

### 安装器临时目录报错（实测）

如果出现：

```text
Setup was unable to create the directory
"C:\Users\<你>\AppData\Local\Temp\is-....tmp".
Error 5: Access denied.
```

这是 UAC 提权后的 TEMP 隔离/权限问题，不是安装包损坏。两种绕过方式：

1. 双击 `dist\installer\Install-AppGuard.bat`：它把 `TEMP/TMP` 指向
   `%SystemRoot%\Temp` 后再启动 setup；
2. 或打开**管理员终端**，执行：

```powershell
cd /d E:\小工具\app_guard\dist\installer
$env:TEMP="$env:SystemRoot\Temp"; $env:TMP="$env:SystemRoot\Temp"
.\AppGuardSetup-0.3.0-x64.exe
```

如果仍然失败，需要改用 per-user 安装器（安装到 `%LOCALAPPDATA%\Programs`，
再用单独的 UAC 助手创建登录任务）；这需要重新设计安装流程。

## 9. 现在不要做/仍需确认

- 代码签名（Authenticode）尚未做；未签名安装包会触发 SmartScreen/杀软提示；
- 干净 Win11 VM、自定义 D 盘安装、卸载保留数据仍需用户/VM 验收；
- 安装器的 UAC 流程没有在自动化里点过“是”；
- 若要分发，建议先签名，并把 `.sha256` 一起发出。
