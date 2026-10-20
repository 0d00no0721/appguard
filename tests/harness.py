"""极简测试脚手架（不引入 pytest，保持零依赖）。"""

from __future__ import annotations

import os
import traceback

_results: list[tuple[str, bool, str]] = []


def work_dir(name: str) -> str:
    """给测试用的固定工作目录。

    ⚠️ **不要用 `tempfile.mkdtemp`/`TemporaryDirectory`**：它们生成的目录名以 `tmp` 开头，
    而本项目的验证沙箱**禁止写入名字以 `tmp` 开头的目录**（见 docs/开发环境说明.md 坑 14）。
    """
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".work", name)
    os.makedirs(root, exist_ok=True)
    return root


def check(label: str, cond: bool, detail: str = "") -> bool:
    _results.append((label, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    line = f"    [{mark}] {label}"
    if not cond and detail:
        line += f"   <- {detail}"
    print(line, flush=True)
    return bool(cond)


def eq(label: str, got, want) -> bool:
    return check(label, got == want, f"got={got!r} want={want!r}")


def section(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def run_module(name: str, func) -> None:
    print(f"\n### {name}", flush=True)
    try:
        func()
    except Exception:                                           # noqa: BLE001
        traceback.print_exc()
        _results.append((f"{name} 执行异常", False, "见上面的 traceback"))


def summary() -> int:
    total = len(_results)
    failed = [r for r in _results if not r[1]]
    print("\n" + "=" * 62)
    print(f"合计 {total} 项断言：通过 {total - len(failed)}，失败 {len(failed)}")
    for label, _ok, detail in failed:
        print(f"  FAIL  {label}  {detail}")
    print("=" * 62)
    return 1 if failed else 0
