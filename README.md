# AppGuard · 应用守护 / 进程账本

> **按软件分组看进程，按进程决定关谁。** 用户不点，工具不动手。
> 工作区约定：本目录是项目根，根目录 `E:\小工具\` 不放散落文件；批处理一律 CRLF。

## 它解决什么

**“我没在用某个软件时，它私自开的那些进程不该在跑。”**

2026-10-17 实例：用户没开雷电模拟器，却被推广弹窗打断。取证发现
`LDPlayerSvr`（LocalSystem、开机自启）内部有 **30 分钟定时器**，用 `CreateProcessAsUser`
在用户桌面拉起 `ld29297.exe --fromserver` → 拉推送 → 弹广告。启动项、计划任务里
**没有任何雷电条目**，所以“任务管理器 → 启动”页里根本关不掉它。

2026-10-18 按用户反馈调整：**动作单位是进程**（一个/一系列私自开启的进程），
不是“整个软件一键关”。工具负责看清楚并给建议，决定权在用户。

## 五条能力线

| 主线 | 需求 | 产物 |
|---|---|---|
| **L1 清单** | 我现在在用哪些软件 | `software.list`：按软件聚合的进程/服务/自启/在用依据 |
| **L2 分类** | 每个进程是干嘛的 | `process.explain`：角色 + 归属 + 来源 + **中文一句话** |
| **L3 清理** | 关掉未在用软件的私自进程 | `cleanup.plan`（行级默认勾选）→ `cleanup.execute {pids}` |
| **KB 闭环** | 越用越准 | `kb review`（待办包）→ `kb apply`（只改 role/note）→ `kb rollback` |
| **Web 网关** | 浏览器前端的本地 HTTP/SSE 接口 | `guardctl web` + `web/` 六屏页面 + `AppGuard.bat` |

**动作单位是进程**；唯一例外是第三方服务的 `stop_service`（服务不是进程，但它是
“定时拉起”的源头；只改运行状态、不改启动类型）。

## 目录结构

```
app_guard/
├── README.md / AGENTS.md         # 本文件 / 操作纲领
├── 接口规范.md                    # ★ UI↔核心 唯一契约（R4 含 Web 网关）
├── 设计文档.md                    # 历史设计稿（旧守护态，未实现；以 README/接口规范为准）
├── docs/
│   ├── 开发环境说明.md             # 沙箱能力 + 已踩的坑
│   ├── UI参考文档.md               # 屏幕集/字段/文案/降级
│   ├── UI搭建Prompt.md             # 历史：给 UI agent 的 prompt
│   ├── Web后端接口.md              # 浏览器前端的 HTTP/SSE 接口、安全、错误映射
│   └── 代码复查报告.md             # 2026-10-17 历史复查（部分结论已被 R3 取代）
├── src/appguard/
│   ├── models.py                  # 枚举/数据结构（含 microsoft/background/private、action）
│   ├── winapi.py                  # 进程/窗口/服务/自启/版本/签名/优雅关闭/停服务
│   ├── classify.py                # L2 分类 + 知识库 + 中文解释
│   ├── inventory.py               # L1 采集/归属聚合/在用/私自标记
│   ├── config.py                  # config.toml（[policy]/[cleanup]/[collect]/[kb]）
│   ├── audit.py                   # 审计 JSONL（序号/轮转/查询/导出）
│   ├── evaluator.py               # L3 判定：微软/关键进程保护、私有判定、按进程计划
│   ├── cleaner.py                 # L3 执行：按 PID、优雅关闭、停服务、dry-run、PID 复用
│   ├── kb.py                      # 知识库：pending 证据 / review 包 / 补丁校验与备份
│   ├── ipc.py                     # NDJSON 信封 / 事件总线 / 管道+TCP
│   ├── daemon.py                  # 装配：采集线程、方法分发、计划表、KB 待办
│   ├── web.py                     # 浏览器后端：本地 HTTP/JSON + SSE + 静态页面 + token/同源校验
│   └── cli.py                     # guardctl（14+ 子命令，含 web）
├── web/                           # 浏览器前端（零依赖、零构建）
│   ├── index.html                 # 壳：侧栏 / 顶栏 / 状态栏 / Toast / Modal
│   ├── app.css                    # 构成主义深色设计系统 + 六屏组件 + 图标描绘动画
│   ├── app.js                     # token / API / SSE / 六屏 / 清理执行 / 设置
│   └── assets/                    # 图标（appguard.ico / 64 / 256 / png）
├── AppGuard.bat                   # 唯一入口：托盘启动器（服务 + Edge 窗口 + 托盘）
├── archive/                       # 旧运行数据归档（可整体删除；见 archive/README.md）
├── packaging/                     # 安装包构建（0.3.0 已构建；见 packaging/README.md）
│   ├── entry.py / AppGuard.spec / AppGuard.iss
│   ├── version_info.txt / build_release.ps1
│   └── README.md
├── ui/                            # tkinter 六屏 + 托盘（保底/旧版；主界面已转浏览器）
│   ├── appguard_ui.py / theme.py / client.py / common.py
│   ├── screens_*.py               # 总览/软件/进程/清理/审计/设置
│   ├── selfcheck_basic.py / selfcheck_select.py / selfcheck_contract.py
│   ├── selfcheck_dryrun.py / selfcheck_lifecycle.py
│   └── README.md
├── tests/                         # 255 项断言
│   ├── fixtures.py                # 复刻 2026-10-17 雷电弹窗事故
│   ├── test_l1l2.py / test_l3.py / test_judgement.py
│   ├── test_ipc.py / test_contract.py / test_kb.py / test_daemon.py
│   ├── test_web.py                # HTTP/token/Origin/SSE/静态页面/错误映射
│   └── run_all.py
└── tools/
    ├── guardctl.py                # CLI 启动器
    ├── appguard_web.py            # 服务探测/启动（守护 + 网关 + Edge 窗口）
    ├── appguard_launcher.py       # 单入口托盘启动器（本文件是 AppGuard.bat 的实现）
    ├── AppGuard-admin.bat         # 一键 UAC：提权安装/启动管理员守护
    ├── install.bat / uninstall.bat # 管理员：注册/删除登录任务、启停守护
    ├── wait_admin.py              # 安装器等待管理员守护就绪
    ├── elevate_install.py         # UAC：ShellExecuteW("runas") 运行 install.bat
    ├── make_shortcut.bat / make_shortcut.py  # 桌面快捷方式（pythonw + 图标）
    ├── acceptance_check.py        # 真机六段验收
    ├── code_review.py             # 静态体检（含 ui/）
    ├── code_probe.py              # 动态探针（全部 IPC 方法）
    ├── dev/                       # CDP 浏览器验收脚本（开发用，非运行依赖）
    ├── smoke_winapi.py / smoke_l1l2.py / smoke_l3.py / smoke_kill.py
    └── debug_grouping.py          # 归属解析调试
```

## 全新状态（2026-10-20 清理后）

- `C:\ProgramData\AppGuard` 已重新初始化：只有默认 `config.toml` + `kb/`，
  **`dry_run=true`**，无 `audit.jsonl` / `daemon.pid` / `state.json`；
- `%LOCALAPPDATA%\AppGuard\web-endpoint.json` 与 `launcher.log` 已移走，首次启动重新生成；
- 旧运行/开发数据归档在 `archive/20261019-1856/` 与 `archive/20261020-0650/`
  （说明见 `archive/README.md`），可整体删除；
- **当前没有 AppGuard 守护/网关/窗口在运行**；登录任务可能仍注册（管理员安装过一次），
  双击 `AppGuard.bat` 会自动使用它；如果没有任务，托盘菜单里选“以管理员安装/重启”。

## 现在能跑什么

```powershell
# 一次性命令（无需常驻进程，进程内直跑）
python tools\guardctl.py doctor          # 环境自检
python tools\guardctl.py software        # L1：在用软件清单
python tools\guardctl.py ps --explain    # L2：进程分类 + 中文解释
python tools\guardctl.py plan            # L3：计划（显示默认勾选/动作/服务）
python tools\guardctl.py clean --yes     # L3：执行默认勾选项（受 dry_run 控制）
python tools\guardctl.py clean --pids 1234 5678   # 只执行指定 PID
python tools\guardctl.py audit -n 30     # 审计
python tools\guardctl.py kb stats        # 知识库待办统计
python tools\guardctl.py kb review       # 生成待办包（report.md + pending.json）
python tools\guardctl.py kb apply patch.json          # 预览补丁
python tools\guardctl.py kb apply patch.json --yes    # 入库（先备份）
python tools\guardctl.py kb rollback <备份路径>       # 从备份恢复

# 唯一入口
AppGuard.bat                                    # 托盘启动器：服务 + Edge 窗口 + 托盘；关闭窗口行为可在托盘设置
tools\AppGuard-admin.bat                        # 首次/恢复：UAC 提权 → 停普通守护 → 起管理员守护 + 登录任务
tools\install.bat                               # 管理员：只注册/启动登录任务（AppGuard-admin 内部调用）
tools\uninstall.bat                             # 管理员：停止守护 + 删除登录任务
tools\make_shortcut.bat                         # 可选：桌面快捷方式（指向 AppGuard 托盘启动器）
python tools\appguard_web.py --dry-run          # 只打印启动命令，不开窗
python tools\guardctl.py web --port 0           # 手动起网关（普通用户）
python tools\guardctl.py --local web            # 开发：进程内直跑，不必先起守护

# 常驻守护 + 旧 tkinter 界面（保底）
python tools\guardctl.py serve --transport auto
python ui\appguard_ui.py --data-dir .appguard-dev

# 验证
python tests\run_all.py                  # 255 项断言
python tools\code_review.py              # 未用导入 0 / 死代码 0
python tools\code_probe.py               # 全部探针通过
python tools\acceptance_check.py         # 真机验收（管理员下更完整）
```

## 安装包（0.3.0，已构建）

```text
dist\installer\AppGuardSetup-0.3.0-x64.exe
dist\installer\AppGuardSetup-0.3.0-x64.exe.sha256
```

- 双击安装，UAC 选“是”；目录页可改到 D 盘；程序文件装到 `{app}`，
  配置/审计/知识库仍固定 `C:\ProgramData\AppGuard`；
- 安装器会停旧守护、复制文件、创建 `AppGuard` 登录任务并立即启动管理员守护；
- 安装完成页的“Launch AppGuard”以**原普通用户**启动（`runasoriginaluser`），
  不会把 Edge 提权；
- 托盘以普通用户运行；只有登录任务/守护是管理员。**不要**以管理员身份运行托盘；
- 未做代码签名，SmartScreen/杀软可能提示；校验 `.sha256`；
- 如果安装器报 `...\Temp\is-...tmp Error 5`，用
  `dist\installer\Install-AppGuard.bat`（把 TEMP 指向 `%SystemRoot%\Temp`），
  或在管理员终端设置 `TEMP/TMP` 后运行 setup；
- 卸载：设置/应用列表或 `{app}\unins000.exe`；默认保留 `C:\ProgramData\AppGuard` 数据。

源码版仍可双击 `AppGuard.bat`；安装包版双击 `{app}\AppGuard.exe`。
构建/验收细节见 `packaging/README.md`。

## 默认行为（与你确认过的口径）

- **微软组件**（签名/厂商 Microsoft，或路径在 Windows/WindowsApps/DriverStore）：
  标“微软”，**永久保留**，不进入可执行计划。
- **关键系统进程**（lsass/csrss/wininit/services/smss/winlogon/svchost…）与守护自身/父链：
  永久保护。
- **更新器/推广/遥测/崩溃收集**：任何软件（包括你正在用的）都标“私自”，默认勾选。
- **托盘/后台常驻/辅助**：软件**未在用**且来源是自启/服务链 → 私自，默认勾选；
  软件**正在用** → 展示但默认不勾（避免拆运行时）。
- **第三方服务**：软件未在用 → 默认勾选，动作“停止服务”；正在用 → 默认不勾。
- **主程序/启动器**：不是私自；未在用残留显示为“需确认”，默认不勾；正在用的直接不作为目标。
- **无法归属/未识别**：显示为待办，不默认勾选；进入知识库闭环。
- **动作**：只动勾选的 PID；默认不连带子进程；有窗口先 `WM_CLOSE` 等 5 秒再强杀；
  `dry_run=true` 连关闭消息都不发。

## 单入口托盘启动器（后端 + 本地页面，复用 Edge）

**日常只需要双击 `AppGuard.bat`**。它启动 `tools\appguard_launcher.py`，一次完成：

1. 探测守护；不可达时优先启动已注册的 `AppGuard` 登录任务（管理员守护）；
   只有**没有任务且没有存活守护**时，才以普通用户后台启动（**只读/演练模式**，不自动提权）；
2. 探测 `web-endpoint.json`；网关不可用时后台启动 `guardctl web`；
3. 已有 `AppGuard · …` 窗口就前置聚焦（单实例），否则用 `msedge --app=<url>`
   打开**无地址栏窗口**——复用已开着的 Edge 配置/进程，不另起 profile、不引 WebView2；
   找不到 Edge 时回退 Chrome / 默认浏览器；
4. 常驻系统托盘，菜单提供：打开界面 / 关闭窗口时（最小化到托盘或退出）/
   以管理员安装或重启 / 创建快捷方式 / 打开日志 / 退出；
5. 关闭 Edge 窗口时，按托盘里的设置执行：
   - **最小化到托盘**（默认）：服务继续跑，托盘图标保留，再双击 `AppGuard.bat` 或托盘
     “打开界面”恢复窗口；
   - **退出 AppGuard**：关闭网关、停止守护、托盘退出（登录任务仍在，下次登录会再启动）；
6. 所有探测/启动/关闭日志写 `%LOCALAPPDATA%\AppGuard\launcher.log`。

**第一次用（要实盘能力）**：双击 `tools\AppGuard-admin.bat`（UAC 选“是”）→ 停普通守护、
注册登录任务并立刻启动管理员守护；然后双击 `AppGuard.bat` → 设置里关闭“演练模式”。
也可以直接在托盘菜单里选“以管理员安装/重启（UAC）”。

浏览器界面（`web/`，零依赖零构建）六屏：

| 屏 | 内容 |
|---|---|
| **总览** | 在用软件 / 私自进程 / 默认可释放 / 受保护；软件构成环图、内存 TOP、私自组件、在用列表 |
| **软件** | 搜索 + 全部/在用/私自/系统筛选；卡片墙；详情弹窗（进程/服务/自启）→ 定向清理 |
| **进程** | 内存地图（面积=内存，颜色=角色）、角色/活跃/来源/软件筛选、逐行解释侧栏、单进程清理 |
| **清理** | 计划期 + 倒计时；默认勾选/需确认分组；行级勾选；演练/实盘提示；执行结果与审计 |
| **审计** | 最新在上、事件筛选、逐行 JSON 详情 |
| **设置** | 演练开关（关前二次确认）、idle 窗口、保护名单增删、知识库状态、守护信息 |

> 截图含本机软件名/路径信息，未随公开仓库分发；本机可在 `docs/screenshots/` 查看。

实现要点：

- **深色构成主义**（`web/app.css`）：硬边卡片、粗线图标、`#6a5cff`/`#5bc0eb` 点缀；
  侧栏图标 hover/选中用 `pathLength=100` + `stroke-dashoffset` 重描一遍；
- **SSE 增量**：`/api/events?since_seq=`；不可见时暂停轮询/重绘，页面隐藏不刷新；
- **无感刷新**：自动/手动刷新走 `softRefresh`，只 patch 数据区域（统计块、卡片、表格行、
  审计行），**不重建 `#screen`**；入场动画只在切换屏幕时触发；保留滚动位置、输入焦点与
  选中行。`snapshot.updated` 只更新计数，整屏数据刷新至少间隔 12 秒，输入中跳过；
- **零内联脚本**：CSP `script-src 'self'`，JS/CSS 都是 `web/` 下的静态文件；
- **令牌只在 fragment**：`#token=…` 不进请求行；JS 读入 `sessionStorage` 后立即
  `history.replaceState` 去掉 hash。

Web 网关安全与接口：

- `POST /api/<method>`：JSON body 即 params；`Authorization: Bearer <token>`；
- `GET /api/events?token=&since_seq=`：SSE 事件流；没有连接时不轮询；
- `GET /`：前端页面；`index.html`/`app.css`/`app.js` 缺任一文件时回退为 API 发现 JSON；
- token/端口写入 `%LOCALAPPDATA%\AppGuard\web-endpoint.json`，不在 ProgramData；
- Host/Origin/Sec-Fetch-Site/CSP/Content-Type 全部校验；不暴露 `daemon.shutdown`/
  `audit.export`/`subscribe`；网关不提权，真实清理仍由守护的 `dry_run`/管理员检查决定。

守护单实例：`daemon.pid` 与 `guardctl stop` 配套，启动器据此避免重复起守护；
`--pipe-name` 必须使用完整 `\\.\pipe\...`（启动器会自动补全裸名）。

细节与示例见 [docs/Web后端接口.md](docs/Web后端接口.md)。

## 硬约束（写进实现的规矩）

1. 核心不弹 UI、不打印面向用户的界面内容；一切对外能力走 IPC。
2. **不提供“杀任意进程”的裸接口**：只能执行计划内、未过期、被选中的 PID。
3. **先 dry-run**：`policy.dry_run=true` 时只记录不动作。
4. **可回滚**：知识库补丁写前备份；本工具不改服务启动类型/注册表/IFEO。
5. 依赖只用 stdlib + ctypes + pywin32；托盘用 ctypes 自绘（`tools/win_tray.py`），
   不依赖 pystray/Pillow；旧 tkinter UI 额外需要 tkinter + pystray/Pillow（缺失降级）。
6. **Web 网关只绑 127.0.0.1**、随机 token、同源校验；浏览器不能直接拿到管理员权限。

## 文档索引

- [AGENTS.md](AGENTS.md) —— 操作纲领与硬约束
- [接口规范.md](接口规范.md) —— 方法/事件/错误码/数据模型/判定口径/验收
- [docs/Web后端接口.md](docs/Web后端接口.md) —— 浏览器前端的 HTTP/SSE 接口与安全
- [packaging/README.md](packaging/README.md) —— 安装包规划（PyInstaller + Inno Setup，暂未执行）
- [设计文档.md](设计文档.md) —— **历史**设计稿（旧守护态）
- [docs/开发环境说明.md](docs/开发环境说明.md) —— 沙箱能看什么/不能看什么
