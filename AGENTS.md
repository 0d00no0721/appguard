# AGENTS.md — AppGuard（应用守护 / 进程账本）

> **本文件是本项目的操作纲领**；细节见同目录 `README.md`、`接口规范.md`、`设计文档.md`。
> 工作区级约定见 `../AGENTS.md`（根目录不放散落文件；`.bat` 必须 CRLF）。

## 这是什么

**“我没在用某个软件时，它私自开的那些进程不该在跑。”**

2026-10-17 的真实事故：用户没开雷电模拟器，却被它的推广弹窗打断工作 ——
`LDPlayerSvr`（LocalSystem、开机自启）内部有 30 分钟定时器，用 `CreateProcessAsUser`
在用户桌面拉起 `ld29297.exe --fromserver` 拉推送弹广告；而**启动项/计划任务里没有任何
雷电条目**，所以“任务管理器 → 启动”页里根本关不掉它。

产品定位（2026-10-18 用户确认）：

> **一本按软件分组的进程账本 + 一把按进程执行的手术刀。**
> 工具负责看清楚（归属 / 职能 / 来源 / 活跃度 / 私自标记）并守住红线；
> 用户负责挑“我现在不想让它跑”的那一个/一组进程。**用户不点，工具不动手。**

能力线（后端 + 浏览器 UI 均已完成；tkinter 为保底）：

| 主线 | 能力 | 入口 |
|---|---|---|
| **L1 清单** | 我现在在用哪些软件（含“在用依据”） | `guardctl software` / UI 总览 |
| **L2 分类** | 每个进程：角色 + 归属 + 来源 + 中文整句 | `guardctl ps --explain` / UI 进程分类 |
| **L3 清理** | 按进程勾选 → 结束进程 / 停止第三方服务 | UI 清理页 / `guardctl plan` → `clean` |
| **KB 闭环** | 未识别进程 → 待办包 → 补丁 → 入库（人工确认） | `guardctl kb review/apply/rollback` |
| **Web 网关** | 浏览器前端的本地 HTTP/SSE 接口 + 六屏页面 | `AppGuard.bat` / `guardctl web` |

**动作单位是进程**，唯一例外是第三方服务（服务不是进程，但它是“定时拉起”的源头，
所以提供 `stop_service`，只改运行状态、不改启动类型）。

## 跑起来 / 验证

```powershell
python tools\guardctl.py doctor        # 环境自检（会诚实报告权限与可见性受限）
python tools\guardctl.py software      # L1
python tools\guardctl.py ps --explain  # L2
python tools\guardctl.py plan          # L3 计划（不执行；显示默认勾选/动作）
python tools\guardctl.py clean --yes   # L3 执行（默认只执行默认勾选项）
python tools\guardctl.py clean --pids 1234 5678   # 只执行指定 PID（行级选择）
python tests\run_all.py                # 255 项断言（合成场景 + 契约 + 判定 + KB + Web）
python tools\code_review.py            # 静态体检（现在也扫 ui/）
python tools\code_probe.py             # 动态探针（畸形参数撞全部 IPC 方法）
python tools\acceptance_check.py       # 真机验收（进程/软件/计划/雷电案例）

AppGuard.bat                            # 唯一入口：托盘启动器（服务 + Edge 窗口 + 托盘）
tools\AppGuard-admin.bat                # 首次/恢复：UAC → 停普通守护 → 起管理员守护 + 登录任务
tools\install.bat                       # 管理员：注册登录任务并立即启动管理员守护
tools\uninstall.bat                     # 管理员：停止守护 + 删除登录任务
tools\make_shortcut.bat                 # 可选：桌面快捷方式（指向托盘启动器）
python tools\appguard_web.py --dry-run  # 只打印启动命令（不开窗）
powershell -File packaging\build_release.ps1  # 测试 + PyInstaller + Inno 安装包 + SHA256
python tools\guardctl.py serve --transport auto   # 常驻守护（管道优先，失败降级 TCP）
python tools\guardctl.py web --port 0             # 浏览器后端：本地 HTTP/SSE（普通用户）
python tools\guardctl.py --local web              # 开发：进程内直跑，不必先起守护
python ui\appguard_ui.py --data-dir .appguard-dev # 旧 tkinter UI（保底，主界面已转浏览器）
```

无参数命令默认**进程内直跑**（不需要常驻守护）；UI/跨进程调用需 `serve`。

## 硬约束（改代码前必读）

1. **没有“杀任意进程”的接口**。只能执行 `cleanup.plan` 产生、未过期、且被选中的计划项；
   `cleanup.execute {plan_id, pids:[...]}` 的 PID 必须属于该计划。
2. **`policy.dry_run` 默认 true**。演练模式连 `WM_CLOSE` 都不发、不停服务。
3. **`plan_pool` 是 L3 候选的唯一来源**，`evaluator.fingerprint()` 必须与它同源。
   `in_use` **只是标签**，不再是候选池闸门（2026-10-18 修正，早期把它当硬条件 ⇒ 候选恒 0）。
4. **`origin` 只是风险/证据输入，不是硬过滤条件**；服务链要**传递**（子进程继承服务来源）。
5. **归属聚合用 `_covers()`（含相等）**；卸载项 `InstallLocation` 必须过 `_is_plausible_root()`；
   服务的 `ImagePath` 必须展开 `%SystemRoot%` / `\SystemRoot\`，否则微软判定与软件名会错。
6. **结束进程用原生 `TerminateProcess`**（`winapi.terminate_process/terminate_tree`；
   按进程执行默认 `kill_tree=false`，子进程单独勾选）；`taskkill` 仅兜底。
7. **微软组件永久保护**：签名/厂商是 Microsoft，或路径在 Windows/WindowsApps 下
   （含 `%SystemRoot%` 展开后的 DriverStore）。关键系统进程（lsass/csrss/…/svchost）与
   守护自身/父链同样永久保护。
8. **本工具不改系统**：不动服务启动类型、不写 IFEO/注册表；唯一的系统动作是
   `stop_service`（只停运行状态）。因此“停止服务”只保证本次会话，重启后自启服务会回来。
9. **知识库补丁只允许改 `role` + `note`**；拒绝 `action/policy/protect/...`。
   `kb apply` 必须显式执行（默认 dry-run，写入前备份），**永远不自动应用**。
10. 依赖只用 **stdlib + ctypes + pywin32**；托盘用 ctypes 自绘（`tools/win_tray.py`），
    不依赖 pystray/Pillow；旧 tkinter UI 额外用 tkinter + pystray/Pillow（缺失时降级）。
11. **Web 网关只绑 127.0.0.1**，每次启动随机 token；校验 Host/Origin/Sec-Fetch-Site；
    不暴露 `daemon.shutdown`/`audit.export`/`subscribe`；token 端点写在 `%LOCALAPPDATA%`。
    网关以**普通用户**运行，通过 IPC 调守护——浏览器不能直接获得管理员权限。
12. **浏览器前端零依赖零构建**：`web/index.html + app.css + app.js`；
    CSP `script-src 'self'`（**不许内联脚本/内联事件处理器**）；令牌只在 URL fragment，
    JS 读入 `sessionStorage` 后立刻去掉 hash；`GET /` 在三个文件缺任一时回退 API 发现 JSON。
13. **启动器不自动提权**：`tools/appguard_web.py` 优先启动已注册的 `AppGuard` 登录任务；
    没有任务且没有存活守护时才起普通用户守护（只读/演练）；`tools/AppGuard-admin.bat`/
    `tools/install.bat` 显式 UAC 提权，提权后先停普通守护再起管理员守护。`daemon.pid` 做单实例；
    `--pipe-name` 必须完整 `\\.\pipe\...`；日志写 `%LOCALAPPDATA%\AppGuard\launcher.log`。
14. **自动刷新禁止重建 `#screen`**：`softRefresh` 只 patch 数据区域（统计块/卡片/表格行/
    审计行），入场动画只加在导航时的 `.screen-inner.enter`；必须保留 `scrollTop`、
    输入焦点和选中行。`snapshot.updated` 只更新计数，数据刷新间隔 ≥12 秒，输入中跳过。
15. **唯一用户入口是 `AppGuard.bat`**（`tools/appguard_launcher.py` + 托盘）：
    关闭 Edge 窗口时按 `%LOCALAPPDATA%\AppGuard\launcher.json` 的 `close_action`
    执行“最小化到托盘”或“退出并停止服务”；单实例用命名互斥量；子进程用
    `console_python()`（pythonw 下 `sys.stdout=None`，不能直接 spawn pythonw 跑 CLI）。
16. **安装器/托盘权限分离**：安装器与登录任务可以管理员；**托盘/Edge 必须普通用户**。
    Inno `[Run]` 启动 AppGuard 用 `runasoriginaluser`；不要以管理员运行托盘。
    安装包构建见 `packaging/`；Inno 安装/卸载需要用户 UAC，自动化不代点。

## 验证环境的坑（详见 `docs/开发环境说明.md`）

- DSH 文件策略会改变系统通道可见性：受限模式只见 25~27 个进程、`tasklist`/WMI/计划任务被拒、
  命名管道客户端被拒；`danger-full-access` 下可见 350+ 进程、管道可用。
  ⇒ 采集覆盖率的验收必须在**真机桌面 + 管理员**下做；
- 测试**不要用** `tempfile.mkdtemp()`（`tmp*` 目录在受限沙箱不可写），用 `tests/harness.work_dir()`；
- 服务 ImagePath 常带未展开环境变量；不展开会把 `wslservice.exe` 当第三方、软件名退化成
  `System32`（2026-10-18 实测）。

## 文档地图

| 文件 | 用途 |
|---|---|
| `README.md` | 项目总览、目录、进度、快速开始 |
| `接口规范.md` | **UI ↔ 核心唯一契约**（R5 含浏览器前端/启动器；R3 按进程选择/服务/KB） |
| `docs/Web后端接口.md` | 浏览器前端的 HTTP/SSE 接口、静态页面、启动器、安全与错误映射 |
| `docs/UI参考文档.md` | 给 UI 的参考：屏幕集、字段、状态语义、文案素材、降级 |
| `docs/开发环境说明.md` | 沙箱能看什么/不能看什么 + 已踩的坑 + 真机验收清单 |
| `packaging/README.md` | 安装包构建/验收（单 exe 多模式 + Inno Setup；0.3.0 已构建） |
| `docs/代码复查报告.md` | 2026-10-17 历史复查（部分结论已被 R3 取代） |
| `设计文档.md` | **历史设计稿**（旧 GUARDED/IN_USE 守护态，未实现；以 README/接口规范为准） |
