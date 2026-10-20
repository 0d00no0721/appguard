"""知识库闭环回归：pending 去重 / review 产物 / 补丁校验 / 备份入库。"""

from __future__ import annotations

import json
import os

from harness import check, eq, section, work_dir

from appguard import kb
from appguard.classify import load_kb


def run() -> None:
    work = work_dir("kb")
    kb_path = os.path.join(work, "known_processes.json")
    for name in os.listdir(work):
        path = os.path.join(work, name)
        if os.path.isfile(path) and (name == "known_processes.json"
                                     or name.startswith("known_processes.json.bak-")):
            os.remove(path)
    store = kb.PendingStore(os.path.join(work, "pending.jsonl"), max_entries=10)
    store.clear()

    section("疑难件证据：去重 + hit_count")
    entry = {"name": "mystery.exe", "exe": r"C:\x\mystery.exe",
             "current_role": "unknown", "confidence": 0.3,
             "reasons": ["fallback"], "cmdline": "--fromserver",
             "services": ["MysterySvc"]}
    first = store.record_many([entry])
    eq("第一次新增", first["added"], 1)
    second = store.record_many([entry])
    eq("第二次只更新", (second["added"], second["updated"]), (0, 1))
    rows = store.load()
    eq("按 name+exe 去重", len(rows), 1)
    eq("hit_count 累加", rows[0]["hit_count"], 2)
    check("证据字段保留", rows[0]["services"] == ["MysterySvc"])

    section("review 待办包")
    review = kb.build_review(work, os.path.join(work, "known_processes.json"),
                             pending_path=store.path, stamp="test")
    check("report.md 已生成", os.path.exists(review["report"]))
    check("pending.json 已生成", os.path.exists(review["pending"]))
    with open(review["report"], encoding="utf-8") as handle:
        report = handle.read()
    check("报告包含进程名与 role 提示", "mystery.exe" in report and "role" in report)
    eq("报告条目计数", review["count"], 1)

    section("补丁校验：只允许 role/note")
    entries, errors = kb.validate_patch({"good.exe": {"role": "updater", "note": "更新器"}})
    eq("合法补丁无错误", errors, [])
    eq("合法补丁规范化", entries["good.exe"], {"role": "updater", "note": "更新器"})
    _, errors = kb.validate_patch({"bad.exe": {"role": "updater", "action": "kill"}})
    check("带 action 的补丁被拒", any("只允许" in e for e in errors), str(errors))
    _, errors = kb.validate_patch({"bad.exe": {"role": "not-a-role", "note": "x"}})
    check("非法 role 被拒", any("非法 role" in e for e in errors), str(errors))
    _, errors = kb.validate_patch({"*.exe": {"role": "updater", "note": "x"}})
    check("通配符/路径被拒", bool(errors), str(errors))
    _, errors = kb.validate_patch({"bad.exe": {"role": "updater", "note": "x" * 200}})
    check("超长 note 被拒", any("超过" in e for e in errors), str(errors))
    _, errors = kb.validate_patch({"bad.exe": {"role": "updater", "note": "x",
                                               "policy": {}}})
    check("想改 policy 的补丁被拒", bool(errors), str(errors))

    section("apply：默认预览，--yes 才写；写前备份")
    patch = {"mystery.exe": {"role": "ad", "note": "某软件的推广组件"}}
    preview = kb.apply_patch(kb_path, patch, dry_run=True)
    check("预览不写盘", not os.path.exists(kb_path) and preview["applied"] is False)
    eq("预览给出 diff", preview["diff"]["added"], ["mystery.exe"])
    applied = kb.apply_patch(kb_path, patch, dry_run=False)
    check("写入后文件存在", os.path.exists(kb_path) and applied["applied"] is True)
    merged = load_kb(kb_path)
    check("load_kb 能读到新条目", merged.get("mystery.exe", ("", ""))[0] == "ad")
    version1 = kb.version_of(merged)
    patch2 = {"mystery.exe": {"role": "ad", "note": "改过的说明"}}
    second = kb.apply_patch(kb_path, patch2, dry_run=False)
    check("第二次写入有备份", bool(second["backup"]) and os.path.exists(second["backup"]),
          str(second.get("backup")))
    merged2 = load_kb(kb_path)
    check("说明已更新", merged2["mystery.exe"][1] == "改过的说明")
    check("版本随内容变化", kb.version_of(merged2) != version1)
    rolled = kb.rollback(kb_path, second["backup"])
    merged3 = load_kb(kb_path)
    check("可从备份回滚", rolled["restored"] and merged3["mystery.exe"][1] == "某软件的推广组件",
          str(merged3.get("mystery.exe")))

    section("非法补丁整包拒绝")
    try:
        kb.apply_patch(kb_path, {"bad.exe": {"role": "updater", "action": "kill"}},
                       dry_run=False)
        check("非法补丁应抛错", False, "没有抛错")
    except ValueError as exc:
        check("错误信息包含原因", "只允许" in str(exc), str(exc))
    with open(kb_path, encoding="utf-8") as handle:
        check("文件仍是合法 JSON", isinstance(json.load(handle), dict))
