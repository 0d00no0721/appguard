# AppGuard · UI 参考文档

> ⚠️ **R5（2026-10-19）**：主界面已转浏览器（`web/` 六屏 + `AppGuard.bat`，复用 Edge
> `--app` 窗口）；浏览器接口/前端结构见 [Web后端接口.md](Web后端接口.md)，本文件继续作为
> 屏幕集/字段/文案/降级的通用参考；旧 tkinter 版见 [ui/README.md](../ui/README.md)。

> ⚠️ **R3（2026-10-18）补充**：清理页已改为**按进程行级勾选**（`default_checked` / `pids`），
> 新增第三方服务行（动作 `stop_service`）、微软/私自标记、知识库闭环；其余屏幕集与降级语义
> 仍然适用。以 [接口规范.md](../接口规范.md) §5.2、§11 与 [ui/README.md](../ui/README.md) 为准。

> 后端已全部就绪（L1 清单 / L2 分类 / L3 清理 / IPC / CLI，已端到端验证）。
> **UI 不需要写任何特权逻辑**——所有进程、服务、注册表操作都在核心侧。
> 本文只是参考：屏幕集、字段、状态语义、文案素材、降级表现、联调方法。

---

## 1. 分工与硬规则

| 角色 | 负责 | 不负责 |
|---|---|---|
| **核心**（已完成） | 采集、分类、风险分级、清理执行、审计、IPC | 任何界面、任何弹窗 |
| **UI**（你来设计） | 信息呈现、交互、通知、设置界面 | 直接调 `taskkill` / SCM / 注册表；自己重算"在用"；自己拼解释文案 |

**四条红线**

1. **不要提供"结束任意进程"的自由入口** —— 后端刻意没有这个接口。所有清理必须走
   `cleanup.plan`（两段式）→ 用户确认 → `cleanup.execute`。
2. **不要自己判断"某软件是否在用"** —— 用 `software.list[].in_use` 与 `in_use_reason`。
   口径唯一，写在核心侧（接口规范 §5.1）。
3. **不要重写解释文案** —— `classification.explanation`、`item.reason`、
   `in_use_reason`、`degraded_reason` 都是**面向用户的中文句子**，直接展示。
4. **不要在后端不可达时展示历史数据** —— 见 §5 降级。

---

## 2. 四种界面状态（图标与文案建议）

| 语义 | 判据（来自 `status.get`） | 建议表现 |
|---|---|---|
| **守护正常** | `health = "ok"` | 常态图标；首页展示"在用软件"与"私自后台 N 个" |
| **降级** | `health = "degraded"` | **顶部横幅 + 图标变色**；文案用 `degraded_reason`（例："未以管理员身份运行：进程/服务信息不完整…"）；**禁用"一键清理"** |
| **演练模式** | `dry_run = true` | 全局水印/角标"演练模式：只记录不动作"；清理按钮文案改为"试运行" |
| **守护未运行** | 连不上（`E_IPC` / 超时） | 空状态页 + "启动方式"说明；**不显示任何进程/软件数据** |

---

## 3. 最小屏幕集（5 屏 + 设置）

### 3.1 总览（首页）
- 数据：`status.get` + `software.list {in_use: true}` + `software.list {has_private: true}`
- 呈现：
  - 大数字：**在用软件数** / **私自后台进程数** / 可释放内存（`CleanupPlan.summary.memory_mb`）
  - "在用"卡片列表：软件名 + `in_use_reason`（例："前台窗口：Visual Studio Code"）
  - "有私自后台"列表：软件名 + 角标 `private_background`
  - 主按钮：**一键清理** → 跳 3.4
- 空态：`（当前没有正在使用的软件）` / `（没有发现私自后台组件）`
- 关键点：**先给"我在用什么"，再给"谁在偷跑"**——这个顺序就是产品要讲的故事

### 3.2 软件清单
- 数据：`software.list`
- 列：状态（在用/未用）· 软件名 · 发布者 · 私自后台数 · 内存 · 进程数 · 服务数 · 自启数
- 点开：`software.get {id}` → 该软件的进程明细、关联服务、自启项（`can_disable=false` 的置灰）
- 排序默认：`在用优先 → 私自后台多优先 → 内存大优先`（后端已排好，直接渲染）

### 3.3 进程分类表（对应"对任务管理器做分类"）
- 数据：`process.list {sort, filter, limit}`，展开时 `process.explain {pids}`
- 列：PID · 进程名 · **角色** · 活跃度 · 来源 · 内存 · 归属软件
- 行悬浮/展开：`classification.explanation`（整句人话）
- 角色中文对照见 §6.2；筛选项直接照搬枚举
- 关键点：**"角色 + 来源"是这屏的灵魂**——任务管理器给不了这个

### 3.4 清理预览与执行（两段式）
- 步骤 1：`cleanup.plan {scope}` → 展示
  - 顶部汇总：`summary.total / safe / caution / protected / memory_mb`
  - 分组：**可安全结束（默认勾选）** / **需确认（默认不勾）** / **受保护（只读展示，附 `why`）**
  - 每项：进程名 · 归属软件 · 内存 · **`reason`**（人话理由）
  - 显示 `expires_at` 倒计时（默认 120 秒）
- 步骤 2：`cleanup.execute {plan_id, include_risk}`
  - `include_risk` 由勾选决定：勾了 safe 就 `["safe"]`，也勾了 caution 才 `["safe","caution"]`
  - 结果：`killed / failed / freed_mb`；失败项显示 `error`（例："PID 已复用…已跳过"）
- 必处理的三种错误（见 §6.3）：`E_PLAN_EXPIRED` / `E_PLAN_STALE` → **回到步骤 1 重新生成**；
  `E_DEGRADED` / `E_PERMISSION` → 提示以管理员运行
- 红线：**绝不做"静默一键杀"**。预览页是产品可信度的全部来源

### 3.5 审计
- 数据：`audit.tail {n}` / `audit.query {since, until, software_id, event}` / `audit.export`
- 列：序号 · 时间 · 事件 · PID · 进程 · 动作 · 结果 · 是否演练
- 事件名中文对照见 §6.4；`dry_run=true` 的行要有"演练"标记
- 用途：用户问"它到底动了什么"时，这屏就是答案

### 3.6 设置
- `policy.get` / `policy.set`：`dry_run`（**默认 true，改动要有二次确认**）、`min_idle_s`、
  `allow_cleanup_while_idle`、`never_touch_roles`
- `protect.add / protect.remove`：`pid:` / `sw:` / `exe:` 三种前缀，做成"添加保护"列表
- 只读展示：`classify.rules`（知识库 126 条，可做成"我们认识哪些进程"）
- 配置文件路径展示即可，**不要让 UI 去写 `config.toml`**（那是人编辑的文件；运行态改动走 `policy.set`，落在 `state.json`）

---

## 4. 事件与实时性

- 连接后 `subscribe {events: [...], since_seq: N}` —— 会先**回放**缓冲区里 `seq > N` 的事件（容量 1000）
- 关键事件：
  | 事件 | 用途 |
  |---|---|
  | `snapshot.updated` | 刷新列表（默认 5 秒一条） |
  | `process.started` / `process.exited` | 增量更新进程表 |
  | `software.started` / `software.stopped` | "在用"列表变化 |
  | `process.blocked` | 实时提示"已拦截 xxx.exe" |
  | `cleanup.executed` | 清理完成 |
  | `heartbeat` | 每 5 秒；**超过 15 秒没收到任何消息 ⇒ 判定守护失联**（切到 §5 的空状态） |
- 拉模式兜底：即使不订阅，`status.get` 也能给出全部计数

---

## 5. 降级与异常处理清单

| 场景 | 表现 |
|---|---|
| 守护未运行 | 空状态页 + 启动指引（`guardctl serve`）；**不展示历史数据** |
| `health = degraded` | 顶部横幅（用 `degraded_reason`）；清理按钮置灰但**仍可浏览**清单与分类 |
| `dry_run = true` | 全局角标；清理按钮文案"试运行"；执行后提示"演练完成，未真正结束任何进程" |
| 计划过期/失效 | 弹提示 + 自动重新 `cleanup.plan`（不要拿旧计划重试） |
| 部分击杀失败 | 逐项显示 `error`；**不要笼统说"清理失败"** |
| IPC 版本不匹配 | `E_API_VERSION` → 提示升级客户端 |

---

## 6. 数据速查

### 6.1 软件（`SoftwareItem`）
`id` · `name` · `publisher` · `version` · `kind` · `install_dir` · `processes[]` · `services[]` ·
`autostart[]` · `in_use` · **`in_use_reason`** · `last_active_at` · **`private_background`** · `memory_mb`

### 6.2 枚举中文对照
- **角色 `role`**：`main` 主程序 / `launcher` 启动器 / `updater` 更新器 / `telemetry` 遥测上报 /
  `crash` 崩溃收集 / `ad` 推广推送 / `tray` 托盘常驻 / `helper` 辅助进程 / `daemon` 后台常驻 /
  `service` 系统服务 / `installer` 安装卸载 / `system` 系统进程 / `unknown` 未知
- **活跃度 `activity`**：`foreground` 前台使用中 / `visible` 有可见窗口 / `tray` 仅托盘 /
  `background` 后台运行 / `service_session` 服务会话
- **来源 `origin`**：`user` 用户启动 / `child_of_user` 用户程序子进程 / `autostart` 开机自启 /
  `service` 服务拉起 / `child_of_service` 服务的子进程 / `unknown` 来源不明
- **风险 `risk`**：`safe` 可安全结束 / `caution` 需确认 / `protected` 受保护
- 后端也提供 `ROLE_ZH/ACTIVITY_ZH/ORIGIN_ZH/RISK_ZH`（`appguard.models`），可参考或直接用 `explanation`

### 6.3 错误码（UI 动作）
| code | UI 动作 |
|---|---|
| `E_PERMISSION` / `E_DEGRADED` | 提示"以管理员身份运行 AppGuard 后再试" |
| `E_PLAN_EXPIRED` / `E_PLAN_STALE` | 重新生成计划 |
| `E_NOT_FOUND` | 刷新列表后重试 |
| `E_TIMEOUT` | 重试一次；仍失败则显示"守护无响应" |
| `E_API_VERSION` | 提示升级 |
| `E_IPC` | 切"守护未运行"空状态 |

### 6.4 审计事件名
`daemon.start` / `daemon.pipe_ready` / `daemon.pipe_error` / `daemon.tcp_ready` / `daemon.stop` /
`cleanup.planned` / `cleanup.start` / `cleanup.dry_run` / `process.blocked` / `process.kill_failed` /
`cleanup.executed` / `config.reloaded`

---

## 7. 联调方法（后端已就绪，不需要等）

```powershell
# ① 不起守护，直接看数据（最快）
python tools\guardctl.py doctor
python tools\guardctl.py software
python tools\guardctl.py ps --explain          # 加 --json 拿原始结构

# ② 起守护，UI 连它（传输自动选择：管道优先，不可用降级回环 TCP）
python tools\guardctl.py serve --transport auto
python tools\guardctl.py --require-daemon status

# ③ 你的 UI 里怎么连
from appguard import ipc
client = ipc.make_client(data_dir)      # 读 endpoint.json → TCP；否则连管道
client.call("hello", api_version=1, client="guardui/0.1.0", token=...)
client.call("software.list", in_use=True)
```

- **不需要管理员权限**也能开发 UI（只读接口都可用）
- 需要构造异常界面时：`policy.set dry_run=false/true`、断掉守护进程看空状态、
  以普通用户启动守护看 `degraded` 横幅

---

## 8. UI 验收清单（一页纸）

- [ ] 五种状态都能显示：正常 / 降级 / 演练 / 守护未运行 / 事件失联
- [ ] "在用软件"卡片显示 `in_use_reason`，不是只显示名字
- [ ] 进程表能一眼区分"角色 + 来源"（这是本产品对任务管理器的核心增量）
- [ ] 清理必须先出预览：safe 默认勾、caution 默认不勾、protected 只读且显示 `why`
- [ ] 计划过期有明确提示并自动重算，不是静默失败
- [ ] 演练模式下按钮文案与执行结果都明确写"未真正结束进程"
- [ ] 审计屏能回答"它到底动了什么"
- [ ] 没有任何"结束任意进程"的自由入口
- [ ] 降级时**不假装在守护**（横幅 + 禁用清理）
