# AppGuard Web 接口与浏览器前端（v0.2 / R5）

> 本文定义 **浏览器前端 ↔ 本地网关** 的接口，以及 `web/` 静态页面与 `AppGuard.bat`
> 启动器。**前端六屏已实现**（2026-10-19）；网关以**普通用户**运行，通过命名管道 /
> 回环 TCP 调守护，不提升权限。接口方法仍在 `接口规范.md` §12 的 R4 白名单内。

## 1. 架构

```
浏览器（复用已开着的 Edge/Chrome，普通用户）
   │  HTTP/JSON + SSE（127.0.0.1 回环）
   ▼
guardctl web（Web 网关，普通用户）+ web/ 静态页面
   │  现有 IPC：NDJSON / named pipe / loopback TCP
   ▼
guardd（守护，管理员或普通用户）+ L1/L2/L3/审计/知识库
```

- 网关**不做业务判断**：所有方法原样转发给 `Dispatcher`；
- 网关**不提升权限**：清理是否真的能执行仍由守护的 `dry_run`/管理员检查决定；
- 前端只读网关数据并展示；判定口径全部来自守护（UI 不重算）。

## 2. 启动

**第一次用（要实盘能力）**：

```powershell
# 双击 tools\AppGuard-admin.bat（UAC 选“是”）
# 它停普通守护 → 注册 AppGuard 登录任务 → 立刻启动管理员守护
# 完成后再双击 AppGuard.bat 打开界面
```

日常：

```powershell
# 推荐：双击 AppGuard.bat
# 或：
python tools\appguard_web.py                 # 守护 + 网关 + 复用 Edge --app 窗口
python tools\appguard_web.py --dry-run       # 只打印将执行的命令
python tools\appguard_web.py --no-open       # 只确保服务
python tools\appguard_web.py --require-admin # 守护不是管理员就退出码 2
python tools\appguard_web.py --data-dir .appguard-dev --endpoint .appguard-dev\web-endpoint-dev.json

# 手动两段式：
python tools\guardctl.py serve --transport auto
python tools\guardctl.py web --port 0
python tools\guardctl.py --local web         # 开发：进程内直跑
```

启动器行为：

1. 读 `<data_dir>/daemon.pid` / 探测 IPC；守护不可达时，若已注册 `AppGuard` 登录任务，
   优先 `schtasks /run` 启动该任务（管理员守护）；只有**没有任务且没有存活守护**时，
   才以普通用户后台启动 `guardctl serve`（只读/演练，不自动提权）；已有 pid 存活则**不重复启动**；
2. 读端点文件并请求 `/api/health`；网关不可用时后台启动 `guardctl web`；
3. 已有标题以 `AppGuard ·` 开头的窗口就 `ShowWindow + SetForegroundWindow`（单实例），
   否则用 `msedge --app=<url>` 打开无地址栏窗口；找不到 Edge 回退 Chrome / 默认浏览器；
4. **不另开浏览器 profile**：复用现有 Edge 进程/配置，只多一个渲染进程；
5. 探测/启动失败写 `%LOCALAPPDATA%\AppGuard\launcher.log`。

**单入口托盘**：源码版 `AppGuard.bat` 只启动 `tools/appguard_launcher.py`；它负责服务、
Edge 窗口和托盘，不再需要用户分别运行多个 bat。关闭 Edge 窗口时按
`%LOCALAPPDATA%\AppGuard\launcher.json`：

```json
{ "close_action": "tray" }
```

- `tray`（默认）：窗口关闭后服务继续跑，托盘保留；再次双击 `AppGuard.bat` 只打开窗口；
- `quit`：关闭网关、停止守护、托盘退出；登录任务仍在，下次登录会再启动。
- 托盘菜单可直接选“以管理员安装/重启（UAC）”“创建桌面快捷方式”“退出并停止 AppGuard”。

输出示例：

```
AppGuard 已就绪：http://127.0.0.1:65037/
  守护 via-gateway · 网关 reused · 动作 browser
  浏览器：C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
```

端点文件（普通用户目录，非 ProgramData）：

```json
{
  "transport": "http",
  "version": "guardweb/0.3.0",
  "host": "127.0.0.1",
  "port": 65037,
  "url": "http://127.0.0.1:65037/#token=...",
  "token": "...",
  "pid": 75128,
  "started_at": "2026-10-19T08:30:00+0800"
}
```

> 令牌在 URL fragment 里，不会出现在 HTTP 请求行/日志中；JS 读入
> `sessionStorage` 后立即 `history.replaceState` 去掉 hash。

## 3. 前端（`web/`）

| 文件 | 作用 |
|---|---|
| `web/index.html` | 壳：侧栏/顶栏/横幅/主区/状态栏/Toast/Modal |
| `web/app.css` | 构成主义深色设计系统：硬边卡片、粗线图标、`#6a5cff`/`#5bc0eb`、响应式 |
| `web/app.js` | token / API / SSE / 六屏 / 图表 / 清理执行 / 设置 |
| `web/assets/` | `appguard.ico`、64/256/1024 PNG |

六屏：

| 屏 | 主要数据 | 交互 |
|---|---|---|
| 总览 | `status.get` + `software.list` + `cleanup.plan` | 统计块、软件构成环图、内存 TOP、私自组件、在用列表 |
| 软件 | `software.list` | 搜索/筛选/排序、卡片墙、`software.get` 详情（进程/服务/自启）→ 定向清理 |
| 进程 | `process.list` | 内存地图（面积=内存、颜色=角色）、筛选、解释侧栏、单进程计划 |
| 清理 | `cleanup.plan` | 计划倒计时、默认勾选/需确认分组、行级勾选、`cleanup.execute {pids}`、结果弹窗 |
| 审计 | `audit.tail` | 最新在上、事件筛选、逐行 JSON 详情 |
| 设置 | `policy.get` / `classify.rules` / `kb.stats` / `status.get` | 演练开关（二次确认）、idle 窗口、保护名单增删、重载配置/知识库、守护信息 |

前端约束：

- **零依赖、零构建、无 CDN、无外部字体**；
- **CSP `script-src 'self'`**：不使用内联脚本/内联事件处理器；
- 动态样式（进度条宽度等）用 `style-src 'unsafe-inline'` 的内联 `style` 属性；
- 侧栏图标 `hover`/选中用 `pathLength=100` + `stroke-dashoffset` 做“重新描绘”动画；
- **无感刷新**：自动/手动刷新走 `softRefresh`，只 patch 数据区域（统计块、卡片、表格行、
  审计行）；**禁止重建 `#screen`**；入场动画只加在导航时的 `.screen-inner.enter`；
  保留 `scrollTop`、输入焦点、选中行；`snapshot.updated` 只更新计数，数据刷新间隔 ≥12 秒；
- 页面隐藏（`visibilitychange`）时暂停轮询/重绘；用户正在输入时跳过自动刷新；
- 静态文件修改后**无需重启网关**（每次请求读盘；`Cache-Control: no-store`）。

## 4. 路由

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/` | 无 | 前端页面；`index.html`/`app.css`/`app.js` 缺任一时回退 API 发现 JSON |
| GET | `/api/health` | Bearer | 网关 PID/uptime + `daemon` 可达性 + `frontend` 状态/文件 |
| POST | `/api/<method>` | Bearer | JSON body 即 params；返回 IPC 信封 |
| GET | `/api/events?since_seq=N&token=...` | token query | SSE 事件流 |
| GET | `/app.css`、`/app.js`、`/assets/*` | 无 | 静态资源（路径穿越被拒） |

`GET /` 回退 JSON 示例：

```json
{
  "ok": true,
  "name": "AppGuard local API",
  "version": "guardweb/0.3.0",
  "api": "/api/<method>",
  "events": "/api/events",
  "health": "/api/health",
  "frontend": "partial",
  "missing": ["app.js"],
  "frontend_root": "E:\\小工具\\app_guard\\web"
}
```

## 5. POST 与错误

```http
POST /api/status.get
Authorization: Bearer <token>
Origin: http://127.0.0.1:<port>
Content-Type: application/json

{}
```

- body 可以直接是 params（`{"limit":3}`），也兼容 `{"params": {...}}`；
- `cleanup.execute` 的 `timeout_ms` 同时用作网关→守护读超时（前端默认 600000）；
- 错误信封与 IPC 一致：

```json
{"ok":false,"error":{"code":"E_PLAN_STALE","message":"计划已失效...","detail":{}}}
```

HTTP 映射：400/401/403/404/409/410/413/415/503/504/500。

## 6. 方法白名单

允许（24 个）：

```
ping, status.get, snapshot.get,
software.list, software.get,
process.list, process.get, process.explain,
classify.rules, classify.reload,
cleanup.plan, cleanup.execute, cleanup.history,
policy.get, policy.set, protect.add, protect.remove,
audit.tail, audit.query,
kb.stats, kb.review, kb.apply, kb.rollback,
daemon.reload
```

刻意**不暴露**：`daemon.shutdown`（浏览器不应该关守护）、`audit.export`
（避免浏览器触发任意路径写盘）、`subscribe`/`unsubscribe`/`hello`（网关自管）。

## 7. SSE

```js
const stream = new EventSource(`/api/events?since_seq=${status.event_seq}&token=${token}`);
```

- 帧格式：`event: <name>\ndata: <IPC 事件 JSON>\n\n`；
- 首帧 `retry: 2000`，每 15 秒 `: ping` 心跳；
- 网关以 **0.8 秒**间隔调用守护的 `subscribe(since_seq)`，把新事件写入流；
  **没有 SSE 客户端时不会轮询**；
- 前端用 `status.get.event_seq` 作初始游标，避免回放 1000 条历史；
- 事件名：`snapshot.updated`、`process.started/exited`、`cleanup.executed`、
  `policy.changed`、`service.stopped`、`kb.*`、`notice` 等；
- 前端在 SSE 断开时自动降级为 10 秒轮询 `status.get`，`EventSource` 重连成功后停止轮询。

## 8. 安全

| 项 | 做法 |
|---|---|
| 绑定 | 仅 `127.0.0.1`（CLI 限制 host 只能是 127.0.0.1/localhost） |
| 端口 | 默认 0=随机；端点文件下发 |
| 认证 | 每次启动随机 32 字节 token；`Authorization: Bearer`；SSE 用 query token |
| 同源 | `Host` 必须是 127.0.0.1/localhost + 当前端口；`Origin` 存在时必须匹配；`Sec-Fetch-Site` 拒绝 cross-site |
| CSRF | 写操作只接受 POST + JSON + Authorization；不返回 CORS 头；OPTIONS 直接 405 |
| 响应头 | `Cache-Control: no-store`、`X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy: no-referrer`、CSP |
| 请求体 | 上限 1 MiB；`Content-Type` 必须 `application/json` |
| 端点文件 | `%LOCALAPPDATA%\AppGuard\web-endpoint.json`；停止时删除 |
| 错误 | 不把 token/内部堆栈写进响应；服务端日志不记录 token |
| 提权 | 网关普通用户；真实结束进程/停服务仍由守护管理员检查与 `dry_run` 决定 |

> 浏览器页面即使被攻击，攻击者也只能调用 24 个白名单方法；且不能绕过
> `dry_run`、计划时效、按 PID 选择与保护名单。

## 9. 权限、单实例与进程标记

- **管理员守护是实盘的前提**：网关/浏览器始终普通用户；`status.get.elevated=false` 时
  只能查看/演练。`tools\AppGuard-admin.bat` 用 UAC 重新运行 `tools\install.bat`：
  `guardctl stop` 停普通守护 → `schtasks /create` 注册登录任务 →
  `schtasks /run` 立即启动 → `tools/wait_admin.py` 轮询到 `elevated=true`；
- **启动器优先任务**：没有守护时若存在 `AppGuard` 登录任务，先 `schtasks /run`，而不是起普通守护；
  只有任务也不存在时才起普通用户守护（只读/演练）；失败写 launcher.log；
- 守护启动写 `<data_dir>/daemon.pid`（`pid/pipe/started_at`），`stop()` 清理；
  启动器据此避免重复起守护（管道不可连时也不重复启动）；
- `--pipe-name` 必须完整 `\\.\pipe\...`；`appguard_web.py` 会把裸名
  `AppGuard.v1` 自动补成 `\\.\pipe\AppGuard.v1`；
- 已有 `AppGuard · 总览/软件/…` 窗口时启动器只聚焦，不再开第二个窗口。

前端对权限状态的提示：

- `elevated=false`：顶部“只读模式” + “如何获得管理员”弹窗（含一键/手动命令）；
  关闭 `dry_run` 会被前端拦下并弹出该帮助；
- `elevated=true && dry_run=true`：横幅提示“管理员已就绪 · 仍是演练模式”，
  提供“去设置关闭演练”；清理页按钮写“演练执行”；
- `elevated=true && dry_run=false`：红色“实盘模式”横幅，按钮写“执行选中”。

## 10. 测试与验收

`tests/test_web.py`（40+ 断言）：发现页/健康、静态页面与 MIME、资源缺失回退、
token、Host/Origin/Sec-Fetch-Site、白名单、Content-Type、超大 body、IPC 错误映射、
SSE 帧、端点文件。全套 **255 项**断言通过。

浏览器端已用 Chrome DevTools Protocol 做过冒烟（2026-10-19）：

- 六屏逐屏截图 + 控制台 `Runtime.exceptionThrown` / `Log.entryAdded` 零错误；
- 软件搜索/筛选、软件详情、定向清理（`scope=software`）、进程角色/搜索筛选、
  审计事件筛选、清理演练（20 项成功、0 失败）、结果弹窗、设置表单读取均通过；
- 权限引导：只读横幅、帮助弹窗、设置页“实盘可用”、非管理员关闭演练被拦截均通过；
- **无感刷新**：全屏 MutationObserver 统计 `screenInner=0`（无 `#screen` 重建）、
  `animationstart( rise )=0`、徽标 childList=0；进程页滚动 420px 后刷新仍为 420；
  搜索框聚焦并输入 `QQ` 后 14 秒焦点/内容保持不变；
- Edge `--app` 窗口打开、第二次启动聚焦、关闭窗口均实测通过。

> 真机「实盘结束进程/停服务」仍需管理员守护，不在普通用户验收范围内。
