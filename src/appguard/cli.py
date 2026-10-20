"""guardctl —— 命令行客户端。

两种后端：
  * `--pipe`（默认自动探测）：连到正在运行的守护进程（命名管道）
  * 无守护时**进程内直跑**：同一个 Dispatcher，无需常驻进程，开箱即用

退出码（接口规范 §7）：0 成功 / 1 用法 / 2 配置 / 3 权限 / 4 未找到 / 5 超时 / 6 守护不可达
                        / 7 降级 / 8 计划过期 / 9 内部错误
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# pythonw.exe（无控制台，计划任务用它启动）会把 stdout/stderr 置为 None，
# 此时任何 print 都会抛 AttributeError —— 兜底到 devnull。
if sys.stdout is None or sys.stderr is None:
    _sink = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _sink
    sys.stderr = sys.stderr or _sink

if __package__ in (None, ""):        # 允许 `python src/appguard/cli.py ...` 直接运行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "appguard"

from . import ipc
from .config import ConfigError, load_config
from .ipc import (E_API_VERSION, E_DEGRADED, E_NOT_FOUND, E_PERMISSION, E_PLAN_EXPIRED,
                  E_PLAN_STALE, E_TIMEOUT, IpcError, PIPE_NAME)
from .models import ACTIVITY_ZH, ORIGIN_ZH, ROLE_ZH

_EXIT = {
    ipc.E_BAD_REQUEST: 1, ipc.E_UNKNOWN_METHOD: 1, ipc.E_API_VERSION: 1,
    "E_CONFIG": 2, E_PERMISSION: 3, E_NOT_FOUND: 4, E_TIMEOUT: 5, "E_IPC": 6,
    E_DEGRADED: 7, E_PLAN_EXPIRED: 8, E_PLAN_STALE: 8, ipc.E_INTERNAL: 9,
}


# --------------------------------------------------------------------------- #
# 后端
# --------------------------------------------------------------------------- #
class LocalBackend:
    """进程内直跑：不需要常驻守护。"""

    name = "local"

    def __init__(self, cfg) -> None:
        from .daemon import Daemon
        self.daemon = Daemon(cfg)
        self.daemon.prepare()

    def call(self, method: str, **params) -> dict:
        response = self.daemon.dispatcher.dispatch({"id": 0, "method": method, "params": params})
        if response.get("ok"):
            return response.get("result") or {}
        error = response.get("error") or {}
        raise IpcError(error.get("code") or ipc.E_INTERNAL,
                       error.get("message") or "未知错误", error.get("detail"))

    def close(self) -> None:
        return None


class RemoteBackend:
    """连到运行中的守护：优先按 endpoint.json 走回环 TCP，否则走命名管道。"""

    name = "remote"

    def __init__(self, data_dir: str, pipe_name: str, *, prefer: str = "auto") -> None:
        # 没有守护时不要卡满 8 秒：CLI 会回落到进程内直跑，1.5 秒足够发现“没起”。
        self.client = ipc.make_client(data_dir, pipe_name, prefer=prefer, timeout=1.5)

    def call(self, method: str, **params) -> dict:
        return self.client.call(method, **params)

    def close(self) -> None:
        self.client.close()


def make_backend(args) -> object:
    if getattr(args, "local", False):
        return LocalBackend(args.config_obj)
    # 传输方式永远交给 make_client 自动决定（endpoint.json → 管道）；
    # require_daemon 只控制"连不上时是否回落进程内直跑"，不控制传输通道。
    try:
        return RemoteBackend(args.config_obj.data_dir, args.pipe_name, prefer="auto")
    except Exception as exc:                                    # noqa: BLE001
        if getattr(args, "require_daemon", False):
            raise IpcError("E_IPC", f"守护进程不可达（{args.pipe_name}）：{exc}") from exc
        if not getattr(args, "quiet", False):
            print(f"（未发现运行中的守护进程，改为进程内直跑；{exc}）", file=sys.stderr)
        try:
            return LocalBackend(args.config_obj)
        except (PermissionError, OSError) as local_exc:
            raise IpcError(
                "E_PERMISSION",
                f"守护未运行，且数据目录不可写（{args.config_obj.data_dir}）：{local_exc}",
            ) from local_exc


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def dump(value, as_json: bool, text_renderer=None) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif text_renderer:
        text_renderer(value)
    else:
        print(json.dumps(value, ensure_ascii=False))


def render_software(payload: dict) -> None:
    items = payload.get("items", [])
    print(f"{'状态':<4} {'私自':>4} {'内存MB':>9}  {'软件':<28} {'发布者':<18} {'进程':>4} {'服务':>4} {'自启':>4}")
    for sw in items:
        print(f"{'在用' if sw['in_use'] else '未用':<4} {sw['private_background']:>4} "
              f"{sw['memory_mb']:>9.1f}  {(sw['name'] or '')[:28]:<28} "
              f"{(sw['publisher'] or '-')[:18]:<18} {len(sw['processes']):>4} "
              f"{len(sw['services']):>4} {len(sw['autostart']):>4}")
        if sw["in_use"]:
            print(f"        ↳ 在用依据：{sw['in_use_reason']}")
    print(f"共 {payload.get('total', len(items))} 个软件")


def render_processes(payload: dict, explain: bool = False) -> None:
    items = payload.get("items", [])
    print(f"{'PID':>7} {'进程':<26} {'角色':<8} {'活跃':<12} {'来源':<16} "
          f"{'内存MB':>8}  {'标记':<8} 归属")
    for proc in items:
        cls = proc.get("classification") or {}
        mark = "微软" if cls.get("microsoft") else ("私自" if cls.get("private") else "")
        print(f"{proc['pid']:>7} {(proc.get('name') or '')[:26]:<26} "
              f"{ROLE_ZH.get(cls.get('role'), cls.get('role')):<8} "
              f"{ACTIVITY_ZH.get(cls.get('activity'), ''):<12} "
              f"{ORIGIN_ZH.get(cls.get('origin'), ''):<16} "
              f"{(proc.get('memory_mb') or 0):>8.1f}  {mark:<8} {cls.get('software_id') or '-'}")
        if explain:
            print(f"        ↳ {cls.get('explanation')}")
    print(f"共 {payload.get('total', len(items))} 个进程")


def render_plan(plan: dict) -> None:
    summary = plan.get("summary") or {}
    print(f"计划 {plan.get('plan_id')}（{plan.get('expires_at')} 过期，指纹 {plan.get('fingerprint')}）")
    print(f"  合计 {summary.get('total')} 项：默认勾选 {summary.get('default_checked', 0)} / "
          f"需确认 {summary.get('caution')} / 受保护 {summary.get('protected')} / "
          f"停止服务 {summary.get('stop_services', 0)}，"
          f"预计释放 {summary.get('memory_mb')} MB（默认勾选 {summary.get('default_memory_mb', 0)} MB）")
    for item in plan.get("items", []):
        action = "停止服务" if item.get("action") == "stop_service" else "结束进程"
        checked = "✓" if item.get("default_checked") else " "
        print(f"  [{checked}] {action:<6} {item['pid']:>7} "
              f"{(item.get('name') or '')[:26]:<26} {item['memory_mb']:>7.1f}MB  "
              f"{item.get('software') or ''}")
        print(f"                理由：{item.get('reason')}")
        if item.get("services"):
            print(f"                服务：{', '.join(item['services'])}")
    skipped = plan.get("protected_skipped") or []
    if skipped:
        print(f"  受保护/跳过 {len(skipped)} 项：")
        for item in skipped[:10]:
            print(f"      {item['pid']:>7} {(item.get('name') or '')[:26]:<26} {item.get('why')}")


def render_audit(payload: dict) -> None:
    for row in payload.get("rows", []):
        flag = " [dry-run]" if row.get("dry_run") else ""
        print(f"#{row.get('seq'):<5} {str(row.get('ts'))[11:23]} {row.get('event'):<20} "
              f"pid={row.get('pid')} {(row.get('name') or '')[:22]:<22} "
              f"{row.get('action') or ''} {row.get('result') or ''}{flag}")


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def cmd_doctor(args) -> int:
    backend = make_backend(args)
    status = backend.call("status.get")
    diag = status.get("diagnostics") or {}
    print("=== AppGuard 自检 ===")
    print(f"后端            : {backend.name}")
    print(f"管理员权限      : {status.get('elevated')}")
    print(f"健康状态        : {status.get('health')}"
          + (f"（{status.get('degraded_reason')}）" if status.get("degraded_reason") else ""))
    print(f"dry_run         : {status.get('dry_run')}")
    print(f"进程可见        : {diag.get('process_count')}（有路径 {diag.get('with_exe')}，"
          f"有命令行 {diag.get('with_cmdline')}，有窗口 {diag.get('with_window')}）")
    print(f"服务            : {diag.get('service_count')}")
    print(f"自启项          : {diag.get('autostart_count')}")
    print(f"已安装软件记录  : {diag.get('uninstall_count')}")
    print(f"计数            : 软件 {status['counts']['software']} / 在用 "
          f"{status['counts']['in_use']} / 私自后台 {status['counts']['private_background']} / "
          f"候选 {status['counts']['candidates']}")
    rules = backend.call("classify.rules")
    print(f"分类知识库      : {rules.get('kb_size')} 条（{rules.get('kb_path')}）")
    print(f"配置            : {args.config_obj.config_path}")
    verdict = []
    if not status.get("elevated"):
        verdict.append("未以管理员运行 ⇒ 进程/服务不可见的部分会被漏掉")
    if diag.get("process_count", 0) < 60:
        verdict.append(f"只看到 {diag.get('process_count')} 个进程 ⇒ 很可能处在受限环境"
                       "（正常桌面应有 150~300 个）")
    if diag.get("with_cmdline", 0) == 0:
        verdict.append("拿不到任何命令行 ⇒ 分类会退化")
    print("体检结论        : " + ("；".join(verdict) if verdict else "一切正常 ✓"))
    return 0


def cmd_status(args) -> int:
    backend = make_backend(args)
    dump(backend.call("status.get"), args.json,
         lambda s: print(f"健康 {s['health']} | 软件 {s['counts']['software']} | "
                         f"候选 {s['counts']['candidates']} | dry_run {s['dry_run']}"))
    return 0


def cmd_software(args) -> int:
    backend = make_backend(args)
    payload = backend.call("software.list", in_use=True if args.in_use else None,
                           has_private=True if args.private else None, kind=args.kind)
    dump(payload, args.json, render_software)
    return 0


def cmd_ps(args) -> int:
    backend = make_backend(args)
    filters = {"software_id": args.software, "role": args.role}
    payload = backend.call("process.list", sort=args.sort, filter=filters, limit=args.limit)
    dump(payload, args.json, lambda p: render_processes(p, explain=args.explain))
    return 0


def cmd_explain(args) -> int:
    backend = make_backend(args)
    payload = backend.call("process.explain", pids=args.pids)
    if args.json:
        dump(payload, True)
        return 0
    for item in payload.get("items", []):
        cls = item.get("classification") or {}
        print(f"{item['pid']:>7} {(item.get('name') or '')[:26]:<26} "
              f"{ROLE_ZH.get(cls.get('role'), '-'):<8} conf={cls.get('confidence')}")
        print(f"        ↳ {cls.get('explanation')}")
        if cls.get("reasons"):
            print(f"        依据：{', '.join(cls['reasons'])}")
    return 0


def cmd_plan(args) -> int:
    backend = make_backend(args)
    params = {"scope": args.scope}
    if args.software:
        params["software_ids"] = args.software
    if args.pids:
        params["pids"] = args.pids
    plan = backend.call("cleanup.plan", **params)
    dump(plan, args.json, render_plan)
    return 0


def cmd_clean(args) -> int:
    backend = make_backend(args)
    plan = backend.call("cleanup.plan", scope="all") if not args.plan else {"plan_id": args.plan}
    plan_id = plan.get("plan_id")
    pids = list(args.pids or [])
    risks = ["safe", "caution"] if (args.include_caution or pids) else ["safe"]
    if not args.yes and not args.json:
        what = f"选中 {len(pids)} 个进程" if pids else f"风险等级：{', '.join(risks)}"
        print(f"将执行计划 {plan_id}，{what}")
        print("（这是演练/dry-run 还是真实执行由 policy.dry_run 决定；加 --yes 跳过确认）")
    params = {"plan_id": plan_id, "include_risk": risks}
    if pids:
        params["pids"] = pids
    result = backend.call("cleanup.execute", **params)
    dump(result, args.json, lambda r: print(
        f"成功 {len(r['killed'])} 个 / 失败 {len(r['failed'])} 个 / 释放 {r['freed_mb']} MB"
        + ("（dry-run）" if r.get("dry_run") else "")))
    return 0


def cmd_audit(args) -> int:
    backend = make_backend(args)
    if args.export:
        payload = backend.call("audit.export", path=args.export, format=args.format)
        print(f"已导出 {payload['rows']} 行 → {payload['path']}")
        return 0
    payload = backend.call("audit.tail", n=args.lines)
    dump(payload, args.json, render_audit)
    return 0


def cmd_policy(args) -> int:
    backend = make_backend(args)
    if args.set:
        key, _, value = args.set.partition("=")
        parsed: object = value
        if value.lower() in ("true", "false"):
            parsed = value.lower() == "true"
        elif value.isdigit():
            parsed = int(value)
        elif key in ("protect", "allow"):
            parsed = [v for v in value.split(",") if v]
        elif "," in value:
            parsed = [v for v in value.split(",") if v]
        payload = backend.call("policy.set", **{key: parsed})
    else:
        payload = backend.call("policy.get")
    dump(payload, args.json, lambda p: print(json.dumps(p, ensure_ascii=False, indent=2)))
    return 0


def cmd_kb(args) -> int:
    backend = make_backend(args)
    if args.kb_command == "stats":
        payload = backend.call("kb.stats")
        dump(payload, args.json)
        return 0
    if args.kb_command == "review":
        payload = backend.call("kb.review")
        if args.json:
            dump(payload, True)
        else:
            print(f"待办包：{payload.get('dir')}")
            print(f"  条目 {payload.get('count')} 条")
            print(f"  报告 {payload.get('report')}")
            print(f"  数据 {payload.get('pending')}")
            print("下一步：把报告交给 agent/人，产出补丁后用 "
                  "`guardctl kb apply <patch.json>`（默认只预览）。")
        return 0
    if args.kb_command == "apply":
        if not args.patch:
            print("缺少补丁文件路径", file=sys.stderr)
            return 1
        payload = backend.call("kb.apply", path=args.patch, dry_run=not args.yes)
        dump(payload, args.json)
        if not args.yes and not args.json:
            diff = payload.get("diff") or {}
            print(f"预览：新增 {len(diff.get('added') or [])} / "
                  f"更新 {len(diff.get('updated') or [])} / "
                  f"未变 {len(diff.get('unchanged') or [])}")
            print("确认无误后加 --yes 真正入库（会先备份）。")
        return 0
    if args.kb_command == "rollback":
        if not args.backup:
            print("缺少备份路径", file=sys.stderr)
            return 1
        payload = backend.call("kb.rollback", backup=args.backup)
        dump(payload, args.json)
        return 0
    return 1


def cmd_init(args) -> int:
    from .config import ensure_data_dir, write_default_config
    cfg = args.config_obj
    ensure_data_dir(cfg.data_dir)
    created = write_default_config(cfg.config_path)
    os.makedirs(os.path.join(cfg.data_dir, "kb"), exist_ok=True)
    print(f"数据目录 : {cfg.data_dir}")
    print(f"配置文件 : {cfg.config_path}（{'已创建' if created else '已存在，未改动'}）")
    print(f"审计日志 : {cfg.audit_path}")
    print(f"知识库   : {cfg.kb_path}（可放同名结构的 JSON 扩展，见接口规范 §4.3）")
    print("下一步   : python tools\\guardctl.py doctor  →  serve --transport auto")
    return 0


def cmd_stop(args) -> int:
    try:
        backend = RemoteBackend(args.config_obj.data_dir, args.pipe_name, prefer="auto")
    except Exception as exc:                                    # noqa: BLE001
        print(f"守护未在运行（{exc}）", file=sys.stderr)
        return 0
    try:
        backend.call("daemon.shutdown")
        print("已请求守护退出")
    finally:
        backend.close()
    return 0


def cmd_serve(args) -> int:
    from .daemon import Daemon
    cfg = args.config_obj
    daemon = Daemon(cfg, pipe_name=args.pipe_name)
    if args.stdio:
        daemon.run_stdio()
        return 0
    try:
        daemon.start(with_server=True, transport=args.transport)
    except (RuntimeError, OSError) as exc:
        print(f"[E] 守护启动失败：{exc}", file=sys.stderr)
        return 1
    info = daemon._ipc_info() or {}
    if info.get("transport") == "tcp":
        print(f"AppGuard 守护已启动：命名管道不可用，已降级为回环 TCP "
              f"{info.get('host')}:{info.get('port')}（端点写在 "
              f"{os.path.join(cfg.data_dir, 'endpoint.json')}）")
    else:
        print(f"AppGuard 守护已启动：管道 {args.pipe_name}（安全描述符：{info.get('security')}）")
    print("Ctrl+C 退出")
    try:
        daemon.wait()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
    return 0


def cmd_web(args) -> int:
    """启动浏览器后端的本地 HTTP/SSE 网关，并托管 web/ 页面。"""
    from .web import DispatcherBackend, IpcBackend, WebBridge
    cfg = args.config_obj
    if args.local:
        from .daemon import Daemon
        daemon = Daemon(cfg, pipe_name=args.pipe_name)
        daemon.prepare()
        backend = DispatcherBackend(daemon)
        mode = "in-process"
    else:
        backend = IpcBackend(cfg.data_dir, args.pipe_name)
        mode = "ipc"
    bridge = WebBridge(backend, host=args.host, port=args.port, token=args.token,
                       endpoint_path=args.endpoint)
    try:
        bridge.start()
    except OSError as exc:
        print(f"Web 网关启动失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"url": bridge.url, "host": bridge.host,
                          "port": bridge.port, "token": bridge.token,
                          "endpoint": bridge.endpoint_path, "mode": mode},
                         ensure_ascii=False, indent=2))
    else:
        print(f"AppGuard Web 网关已启动（{mode}）")
        print(f"  页面/API : {bridge.base_url}")
        print(f"  带令牌 URL: {bridge.url}")
        print(f"  token    : {bridge.token}")
        print(f"  端点文件  : {bridge.endpoint_path}")
        frontend = bridge.frontend_status()
        print(f"  前端资源  : {frontend['status']}（{frontend['root']}）")
        if frontend["status"] != "ready":
            print(f"  缺少文件  : {', '.join(frontend['missing']) or '—'}")
        print("  用上面的带令牌 URL 在浏览器打开；Ctrl+C 退出")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="guardctl", description="AppGuard 命令行")
    parser.add_argument("--config", help="config.toml 路径")
    parser.add_argument("--data-dir", help="数据目录（默认 %%ProgramData%%\\AppGuard）")
    parser.add_argument("--pipe-name", default=PIPE_NAME, help="命名管道名")
    parser.add_argument("--local", action="store_true", help="强制进程内直跑（不连守护）")
    parser.add_argument("--require-daemon", "--pipe", action="store_true", dest="require_daemon",
                        help="必须连上运行中的守护进程（连不上就报错，不回落进程内直跑）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="不打印提示")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)
    sub.add_parser("status", help="守护状态").set_defaults(func=cmd_status)
    sub.add_parser("init", help="初始化数据目录与默认配置").set_defaults(func=cmd_init)
    sub.add_parser("stop", help="让运行中的守护退出").set_defaults(func=cmd_stop)

    p = sub.add_parser("software", help="L1：在用软件清单")
    p.add_argument("--in-use", action="store_true", help="只看在用")
    p.add_argument("--private", action="store_true", help="只看有私自后台的")
    p.add_argument("--kind", help="app/game/runtime/system")
    p.set_defaults(func=cmd_software)

    p = sub.add_parser("ps", help="L2：进程分类")
    p.add_argument("--sort", default="memory", choices=["memory", "cpu", "pid", "name"])
    p.add_argument("--software", help="只看某个软件（id）")
    p.add_argument("--role", help="只看某个角色")
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--explain", action="store_true", help="显示中文解释")
    p.set_defaults(func=cmd_ps)

    p = sub.add_parser("explain", help="解释指定进程")
    p.add_argument("pids", nargs="+", type=int)
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("plan", help="L3：生成清理计划（不执行）")
    p.add_argument("--scope", default="all", choices=["all", "software", "pids"])
    p.add_argument("--software", nargs="*")
    p.add_argument("--pids", nargs="*", type=int)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("clean", help="L3：执行清理（默认只执行默认勾选项）")
    p.add_argument("--plan", help="已有计划 id（省略则现场生成）")
    p.add_argument("--pids", nargs="*", type=int,
                   help="只执行这些 PID（行级选择；自动放开 safe+caution）")
    p.add_argument("--include-caution", action="store_true", help="连需确认的一起清")
    p.add_argument("--yes", action="store_true", help="跳过确认提示")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("audit", help="审计日志")
    p.add_argument("-n", "--lines", type=int, default=30)
    p.add_argument("--export", help="导出到文件")
    p.add_argument("--format", default="jsonl", choices=["jsonl", "csv"])
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("policy", help="查看/修改策略")
    p.add_argument("--set", help="例如 --set dry_run=false")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("kb", help="知识库闭环：stats / review / apply")
    kb_sub = p.add_subparsers(dest="kb_command", required=True)
    kb_sub.add_parser("stats", help="待办与知识库统计").set_defaults(func=cmd_kb)
    kb_sub.add_parser("review", help="生成待办包（report.md + pending.json）").set_defaults(
        func=cmd_kb)
    kb_apply = kb_sub.add_parser("apply", help="校验并合并补丁（默认只预览）")
    kb_apply.add_argument("patch", help="补丁 JSON 路径")
    kb_apply.add_argument("--yes", action="store_true", help="真正写入（会先备份）")
    kb_apply.set_defaults(func=cmd_kb)
    kb_rollback = kb_sub.add_parser("rollback", help="从 .bak-* 备份恢复知识库")
    kb_rollback.add_argument("backup", help="备份文件路径")
    kb_rollback.set_defaults(func=cmd_kb)

    p = sub.add_parser("serve", help="启动守护进程")
    p.add_argument("--stdio", action="store_true", help="以 stdio 方式运行（开发）")
    p.add_argument("--transport", default="auto", choices=["auto", "pipe", "tcp"],
                   help="auto=管道优先、不可用则回环 TCP")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("web", help="启动浏览器后端的本地 HTTP/SSE 网关（普通用户）")
    p.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    p.add_argument("--port", type=int, default=0, help="0=随机端口")
    p.add_argument("--token", help="固定 token（默认每次随机）")
    p.add_argument("--endpoint", help="endpoint JSON 路径（默认 LocalAppData）")
    p.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.config_obj = load_config(args.config, data_dir=args.data_dir)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    for warning in args.config_obj.warnings:
        if not args.quiet:
            print(f"[warn] {warning}", file=sys.stderr)
    try:
        return int(args.func(args) or 0)
    except IpcError as exc:
        print(f"[{exc.code}] {exc.message}", file=sys.stderr)
        return _EXIT.get(exc.code, 9)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
