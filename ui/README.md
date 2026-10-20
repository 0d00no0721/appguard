# AppGuard 桌面 UI（guardui）

> **2026-10-19：主界面已转浏览器**（`web/` 六屏 + `AppGuard.bat`，复用 Edge `--app`）。
> 本目录的 tkinter 六屏保留为**保底/旧版**：后端能力同源，视觉上限受 Tk/GDI 限制。

tkinter 桌面界面，**只通过 IPC 契约驱动后端**（`appguard.ipc` + `appguard.models`，
不 import 任何采集/分类/清理实现，不直接做任何特权操作）。

视觉层集中在 `ui/theme.py`：配色/字体常量 + Canvas 手绘圆角控件
（`RoundedButton` / `NavButton` / `Pill` / `Card` / `SectionCard`），ttk 全局走 clam。

## 启动

```powershell
# ① 先起守护（另开窗口常驻；生产默认数据目录 %ProgramData%\AppGuard）
python tools\guardctl.py serve --transport auto

# ② 起 UI（--data-dir 必须与守护一致；管道模式只影响 endpoint/TCP）
python ui\appguard_ui.py                                  # 生产：默认 %ProgramData%\AppGuard
python ui\appguard_ui.py --data-dir .appguard-dev         # 开发
```

守护没起也能开 UI：显示空状态页 + 启动指引，**不展示任何历史数据**，每 5 秒自动重试连接。

## 依赖

- Python 3.12（本机 `D:\python\python.exe`）
- tkinter（随 Python 自带）
- pystray + Pillow（托盘；**缺失时自动退化为“最小化到任务栏”**）
- pywin32（后端既有依赖；UI 不直接 import）

## 屏幕与契约映射（R3）

| 屏幕 | 数据来源 | 要点 |
|---|---|---|
| 总览 | `status.get` / `software.list` / `cleanup.plan` | 三个大数字；“在用”卡片必显 `in_use_reason`；私自后台只列**未在用**软件；「可释放内存」用 `summary.default_memory_mb` |
| 软件清单 | `software.list` / `software.get` | 删除线/配色区分在用/私自；双击明细（进程/服务/自启）；有私自进程时出现「查看并清理它的 N 个私自进程」→ 只过滤该软件的清理预览 |
| 进程分类 | `process.list` / `process.explain` | 列：PID/进程/角色/活跃度/来源/**标记（微软/私自）**/内存/归属/风险；选中行展示 `explanation` 原文 |
| 清理 | `cleanup.plan` → `cleanup.execute {pids}` | **行级勾选**：第一列点击切换 ☑/☐；默认来自 `default_checked`；动作列区分“结束进程/停止服务”；「全选默认」「清空勾选」；`expires_at` 倒计时；`E_PLAN_EXPIRED/STALE` 自动重生成；逐项结果 |
| 审计 | `audit.tail/query/export` | 序号·时间·事件·PID·进程·动作·结果·演练；双击看完整 JSON；可导出 |
| 设置 | `policy.get/set` / `protect.add/remove` / `classify.rules` | 策略**真实值**（R3 扁平返回）；`dry_run` 改动二次确认；保护名单支持 `pid:` `sw:` `exe:`；知识库版本/规则 ID；重载配置与知识库 |
| 托盘 | pystray | 四态：灰=守护未运行 / 红=降级 / 绿=**有待清理**（存在私自进程）/ 蓝=正常；菜单：打开主界面·进入使用态·临时放行·查看日志·设置·退出 |

红线落实：无「结束任意进程」入口；「在用」只用后端 `in_use`；解释类文案原文展示；
`health=degraded` → 顶部横幅 + 清理按钮置灰；`dry_run=true` → 全局角标 + 按钮改「试运行」，
执行结果明说「未真正结束任何进程、未停止任何服务」。

## 自检脚本（需先起守护，用 `.appguard-dev` 或与守护一致的 data-dir）

```powershell
python ui\selfcheck_basic.py       # 23 项：连接/六屏数据/计划默认勾选/降级横幅/失联重连
python ui\selfcheck_select.py      # 7 项：行级勾选 → 只执行选中的 2 个 PID（演练）
python ui\selfcheck_contract.py    # 6 项：设置页策略真实值 + 保护名单增删
python ui\selfcheck_dryrun.py      # 11 项：健康守护下 dry-run 全流程（需管理员守护）
python ui\selfcheck_lifecycle.py   # 6 项：冷启动空状态/自动连接/断守护失联（需外部启停）
```

实测（2026-10-18，本机）：
- `selfcheck_basic` **23/23**（非管理员守护；降级分支按契约正确置灰）
- `selfcheck_select` **7/7**：勾选 2 个 → 执行结果只含这 2 个，文案明说演练
- `selfcheck_contract` **6/6**：设置页策略真实值、保护名单加/删
- `selfcheck_lifecycle` 6/6（此前记录）
- `selfcheck_dryrun` 需管理员守护（非管理员会按 degraded 正确失败）

## 已知限制

1. **非管理员**：能看/能计划/能演练；真实结束进程会被 `E_PERMISSION` 拒绝；
   部分系统进程 exe 不可读，归入「系统组件（路径不可读）」，不硬猜。
2. **不自动清理**：没有常驻看门狗；用户不点按钮，工具不动手。
3. **停服务不持久**：只停运行状态，不改启动类型；重启后自启服务仍会回来。
4. **微软组件永久保留**：Edge/OneDrive/WSL 等微软签名组件也不在可清理列表里。
5. **主程序不作为默认目标**：正在用的主程序/启动器不显示为可清理项；未在用残留显示但默认不勾。
6. **事件**：后端现在是真推送（R2 已修）；UI 仍保留 4 秒轮询回放作为兼容兜底
   （`EventStream`），事件延迟 ≤4 秒，不影响主流程。
7. 解释面板在默认窗口高度下可能需要滚动；窗口可拉大。
