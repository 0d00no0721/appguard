# UI 搭建 Prompt（可直接交给前端/UI agent）

> ⚠️ **历史 prompt（2026-10-17）**：R3（2026-10-18）后清理页已改为**按进程行级勾选**
> （`default_checked` / `cleanup.execute {pids}`），新增第三方服务 `stop_service`、微软/私自标记、
> 知识库闭环；`policy.get`/`software.get`/`process.get` 返回已扁平化。以
> [接口规范.md](../接口规范.md) §5.2/§11 与 [ui/README.md](../ui/README.md) 为准。

> 用法：把下面「正文」整段复制给实现 UI 的 agent。它自带上下文与验收标准，不需要读后端实现代码。
> 配套文档：`接口规范.md`（契约）、`docs/UI参考文档.md`（屏幕集与素材）、`README.md`（怎么跑后端）。

---

## 正文

你是 Windows 桌面前端工程师。**AppGuard 的后端已全部完成并通过 110 项断言测试**，
你的任务**只做界面**，通过既有 IPC 契约驱动它。

### 0. 先读这三份（按顺序，别读实现）

1. `E:\小工具\app_guard\接口规范.md` —— **唯一契约**：传输层与自动降级、22 个方法、10 类事件、
   17 个错误码、数据模型、判定口径（§5）
2. `E:\小工具\app_guard\docs\UI参考文档.md` —— 屏幕集、字段、状态语义、**文案素材**、降级清单、一页纸验收
3. `E:\小工具\app_guard\README.md` 的「现在能跑什么」—— 怎么起后端、怎么造数据

> 不要读 `src/appguard/` 下的实现（inventory / classify / evaluator / cleaner / daemon / winapi）。
> 你只需要契约；读实现会诱导你绕过 IPC。

### 1. 产品是什么

一句话：**"我没在用某个软件时，它不该在后台跑。"**

- **L1** 我现在在用哪些软件（含"在用依据"）
- **L2** 每个进程是干嘛的（角色 / 归属 / 来源 / 中文整句解释）
- **L3** 一键关掉未在用软件的私自组件（两段式：先预览再执行）

起因：雷电模拟器的**服务定时器**每 30 分钟在用户桌面弹广告，启动项里查不到，
任务管理器的"启动"页也关不掉它。这个工具就是用来终结这类行为的。

### 2. 技术栈与目录（硬性）

- Python 3.12（本机 `D:\python\python.exe`）；**tkinter** 做窗口；**pystray + Pillow** 做托盘
  （Pillow 已装；pystray 需 `pip install pystray`；**装不上就退化为"最小化到任务栏"模式**并在 README 说明）
- 代码放 `E:\小工具\app_guard\ui\`，入口 `ui\appguard_ui.py`，接受 `--data-dir` 参数
  （默认 `%ProgramData%\AppGuard`）
- **只允许 import** `appguard.ipc`（连接与协议）与 `appguard.models`（中文标签字典 `ROLE_ZH/ACTIVITY_ZH/ORIGIN_ZH/RISK_ZH`）
- **禁止 import** inventory / classify / evaluator / cleaner / daemon / winapi
- 不引入 Web 框架、Electron、Qt；依赖越少越好

### 3. 连接方式（唯一生产路径）

```python
import sys
sys.path.insert(0, r"E:\小工具\app_guard\src")
from appguard import ipc

client = ipc.make_client(data_dir)          # 先读 endpoint.json→回环 TCP，否则连命名管道
client.call("hello", api_version=1, client="guardui/0.1.0")   # 必须先握手
data = client.call("software.list", in_use=True)
```

- 后端启动：`python tools\guardctl.py serve --transport auto`
- 未握手就调方法 → `E_NO_HANDSHAKE`；版本不匹配 → `E_API_VERSION`
- 事件：`client.call("subscribe", events=[...], since_seq=0)` 会**先回放缓冲区**再转实时；
  `heartbeat` 每 5 秒一条，**超过 15 秒没收到任何消息即判定守护失联**
- 分支判断一律用 `error.code`；`error.message` 是可直接展示的中文，但**不要用它做逻辑判断**

### 4. 必须实现的界面

| # | 屏幕 | 数据来源（方法） | 要点 |
|---|---|---|---|
| 1 | **总览** | `status.get` + `software.list {in_use:true}` + `software.list {has_private:true}` | 三个大数字：在用软件数 / 私自后台进程数 / 可释放内存；"在用"卡片**必须显示 `in_use_reason`**（例："前台窗口：Visual Studio Code"）；主按钮 → 跳清理预览 |
| 2 | **软件清单** | `software.list`、`software.get {id}` | 列：状态 · 软件 · 发布者 · 私自后台数 · 内存 · 进程 · 服务 · 自启；点开看该软件的进程/服务/自启明细；`can_disable=false` 的自启项置灰 |
| 3 | **进程分类表** | `process.list {sort,filter,limit}` + `process.explain {pids}` | 列：PID · 进程 · **角色** · 活跃度 · **来源** · 内存 · 归属；展开显示 `classification.explanation`（整句中文，**直接展示，不要重写**）；支持按角色/软件筛选 |
| 4 | **清理预览与执行** | `cleanup.plan` → `cleanup.execute {plan_id, include_risk}` | **两段式**。预览页：safe 默认勾选、caution 默认不勾、protected **只读**且显示 `why`；显示 `expires_at` 倒计时；执行后逐项显示成功/失败与 `error` |
| 5 | **审计** | `audit.tail {n}`、`audit.query`、`audit.export` | 列：序号 · 时间 · 事件 · PID · 进程 · 动作 · 结果 · 是否演练；能回答"它到底动了什么" |
| 6 | **设置** | `policy.get/set`、`protect.add/remove`、`classify.rules` | `dry_run` 改动**必须二次确认**；protect 支持 `pid:` / `sw:` / `exe:` 三种前缀；`classify.rules` 可做成"我们认识哪些进程"只读视图 |
| 7 | **托盘** | 同上 | 四态图标：守护正常 / 使用中 / 降级 / 守护未运行；菜单：进入使用态 · 临时放行(15/30/60 分钟) · 查看日志 · 设置 · 退出 |

### 5. 红线（违反即返工）

1. **不提供"结束任意进程"的自由入口** —— 后端刻意没有这个接口；清理必须走 `plan → 用户确认 → execute`
2. **不自己判断"某软件是否在用"** —— 用 `in_use` / `in_use_reason`
3. **不重写解释文案** —— `explanation` / `reason` / `in_use_reason` / `degraded_reason` 直接展示
4. **守护不可达时不得展示任何历史数据** —— 显示空状态 + 启动指引
5. **`health=degraded` 必须显著提示并禁用"一键清理"** —— 不要假装在守护；文案直接用 `degraded_reason`
6. **`dry_run=true` 时**：全局角标 + 按钮文案改"试运行"，执行后明说"未真正结束任何进程"
7. 计划过期/失效（`E_PLAN_EXPIRED` / `E_PLAN_STALE`）→ 提示并**自动重新生成**，不要拿旧计划重试
8. 权限类错误（`E_PERMISSION` / `E_DEGRADED`）→ 提示"以管理员身份运行 AppGuard 后再试"

### 6. 验收（自己跑通并把结果写进 `ui/README.md`）

- [ ] 未起守护时打开 UI → 空状态页，不崩、不显示假数据
- [ ] 起守护后六屏都有真实数据；进程表能一眼区分"角色 + 来源"
- [ ] 预览页三类风险正确分区；protected 只读且显示原因
- [ ] 演练模式下完整走一遍"预览 → 执行"，结果文案明说未真正结束进程
- [ ] 制造降级（以普通用户启动守护）→ 横幅出现、清理按钮置灰
- [ ] 断开守护 → 15 秒内切到失联状态
- [ ] 审计屏能回答"它到底动了什么"
- [ ] 自查未越界：`grep -rn "from appguard" ui/` 只应出现 `ipc` 与 `models`

### 7. 交付物

- `ui/appguard_ui.py`（必要时拆分为多个模块）
- `ui/README.md`：启动方式、依赖安装、已知限制（例如 pystray 缺失时的退化行为）、验收结果
- 截图或录屏（可选，推荐）

### 8. 不要做

- **不改后端任何文件**；发现契约有问题**报告出来**，不要绕过 IPC 直连内部模块
- 不做账号体系、云同步、多语言
- 不做"美化版任务管理器"式的进程浏览器（那是另一个产品；本产品的核心是**策略与意图**）

### 附：命令速查

```powershell
$py = 'E:\小工具\app_guard'

# 起后端（另开一个窗口常驻）
python $py\tools\guardctl.py serve --transport auto

# 开发期先用这些命令确认字段名与嵌套结构，再写渲染代码
python $py\tools\guardctl.py --json software
python $py\tools\guardctl.py --json ps --explain --limit 5
python $py\tools\guardctl.py --json plan
python $py\tools\guardctl.py --json audit -n 5
python $py\tools\guardctl.py doctor

# 后端回归（110 项，别改坏）
python $py\tests\run_all.py
python $py\tools\acceptance_check.py
```
