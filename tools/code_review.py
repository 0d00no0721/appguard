"""代码复查工具：在没有 linter 的环境里做一轮结构化体检。

检查项：
  1. 未使用的 import（按文件统计标识符引用）
  2. 死代码候选（项目内从未被引用的函数/方法名）
  3. 静默失败（`except ...: pass` / `continue`）
  4. 裸 except
  5. 可变默认参数
  6. 超长行（>110）
  7. 重复的中文字典字面量（标签表漂移风险）
  8. TODO/FIXME/XXX

用法：python tools/code_review.py [--all]
"""

from __future__ import annotations

import ast
import collections
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("src", "tools", "tests", "ui")

SKIP_DIRS = {"__pycache__", ".work", ".appguard-dev", ".tmp"}


def iter_py_files() -> list[str]:
    out: list[str] = []
    for base in SCAN_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py"):
                    out.append(os.path.join(dirpath, name))
    return sorted(out)


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


class FileFacts(ast.NodeVisitor):
    def __init__(self, path: str, tree: ast.AST) -> None:
        self.path = path
        self.tree = tree
        self.imports: list[tuple[str, str, int]] = []      # (imported_as, full, lineno)
        self.defs: list[tuple[str, int]] = []              # (qualified-ish name, lineno)
        self.used: set[str] = set()
        self.silent_handlers: list[tuple[int, str]] = []
        self.bare_excepts: list[int] = []
        self.mutable_defaults: list[tuple[str, int]] = []
        self.long_lines: list[int] = []
        self.markers: list[tuple[int, str]] = []
        self.string_dicts: list[tuple[str, int]] = []

    # ---------------------------------------------------------------- #
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.asname or alias.name.split(".")[0], alias.name, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports.append((alias.asname or alias.name, f"{node.module}.{alias.name}",
                                 node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defs.append((node.name, node.lineno))
        args = node.args
        for default in list(args.defaults) + [d for d in args.kw_defaults if d]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.mutable_defaults.append((node.name, node.lineno))
            if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) \
                    and default.func.id in ("list", "dict", "set"):
                self.mutable_defaults.append((node.name, node.lineno))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Name(self, node: ast.Name) -> None:
        self.used.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.used.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for marker in ("TODO", "FIXME", "XXX", "HACK"):
                if marker in node.value:
                    self.markers.append((node.lineno, node.value.strip()[:90]))
                    break
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        # 注意：中文标签表往往「键是 ASCII、值是中文」（如 {"main": "主程序"}），
        # 所以键和值都要看。
        strings: list[str] = []
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                strings.append(key.value)
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                strings.append(value.value)
        chinese = [s for s in strings if any("\u4e00" <= ch <= "\u9fff" for ch in s)]
        if len(chinese) >= 4:
            sample = "、".join(chinese[:3])
            self.string_dicts.append((f"{len(chinese)} 个中文串（如 {sample}）", node.lineno))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # `__all__ = ["C_OK", ...]` 是显式再导出：这些名字算“已使用”。
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                for element in node.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        self.used.add(element.value)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.bare_excepts.append(node.lineno)
        body = [n for n in node.body if not isinstance(n, ast.Expr)
                or not isinstance(n.value, ast.Constant)]
        if body and all(isinstance(n, (ast.Pass, ast.Continue)) for n in body):
            kind = "pass" if isinstance(body[0], ast.Pass) else "continue"
            self.silent_handlers.append((node.lineno, kind))
        self.generic_visit(node)


def main() -> int:
    files = iter_py_files()
    facts: dict[str, FileFacts] = {}
    global_names: collections.Counter[str] = collections.Counter()

    for path in files:
        source = open(path, "r", encoding="utf-8").read()
        tree = ast.parse(source, filename=path)
        item = FileFacts(path, tree)
        item.visit(tree)
        item.long_lines = [i + 1 for i, line in enumerate(source.splitlines())
                           if len(line) > 110]
        facts[path] = item
        for name, _lineno in item.defs:
            global_names[name] += 1

    # 项目级引用表：所有文件里出现过的标识符（含属性名）
    all_used: set[str] = set()
    for item in facts.values():
        all_used |= item.used

    problems = 0

    def report(title: str, rows: list[str]) -> None:
        nonlocal problems
        print(f"\n=== {title} ===")
        if not rows:
            print("  （无）")
            return
        problems += len(rows)
        for row in rows:
            print("  " + row)

    unused_imports: list[str] = []
    for path, item in facts.items():
        for alias, full, lineno in item.imports:
            if alias == "*" or full.startswith("__future__"):
                continue                      # `from __future__ import annotations` 是编译指令
            if alias not in item.used:
                unused_imports.append(f"{rel(path)}:{lineno}  {alias}  (from {full})")
    report("未使用的 import", unused_imports)

    dead_defs: list[str] = []
    for path, item in facts.items():
        for name, lineno in item.defs:
            if name.startswith("__") and name.endswith("__"):
                continue
            if name in ("run", "main", "instate") or name.startswith("visit_"):
                continue                      # 入口函数 / ast/ttk 框架回调
            uses = 0
            for other_path, other in facts.items():
                if name in other.used:
                    uses += 1
            if uses == 0:
                dead_defs.append(f"{rel(path)}:{lineno}  {name}()")
    report("死代码候选（项目内无引用；getattr/字符串调用会误报）", dead_defs)

    silent: list[str] = []
    for path, item in facts.items():
        for lineno, kind in item.silent_handlers:
            silent.append(f"{rel(path)}:{lineno}  except → {kind}")
    report(f"静默失败（except: {len(silent)} 处，需逐个确认是否合理）", silent[:40]
           + ([f"... 共 {len(silent)} 处"] if len(silent) > 40 else []))

    report("裸 except", [f"{rel(p)}:{ln}" for p, i in facts.items() for ln in i.bare_excepts])

    report("可变默认参数",
           [f"{rel(p)}:{ln}  {name}()" for p, i in facts.items()
            for name, ln in i.mutable_defaults])

    long_lines = [f"{rel(p)}:{ln}" for p, i in facts.items() for ln in i.long_lines]
    report(f"超长行（>110 字符，共 {len(long_lines)}）", long_lines[:20])

    dicts: list[str] = []
    for path, item in facts.items():
        for desc, lineno in item.string_dicts:
            dicts.append(f"{rel(path)}:{lineno}  {desc}")
    report("含 ≥4 个中文键的字面字典（标签表漂移风险）", dicts)

    report("TODO/FIXME 标记",
           [f"{rel(p)}:{ln}  {text}" for p, i in facts.items() for ln, text in i.markers])

    print(f"\n{'=' * 70}")
    print(f"扫描 {len(files)} 个文件，问题条目 {problems} 条")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
