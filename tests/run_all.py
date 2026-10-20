"""跑全部测试：`python tests/run_all.py`（不需要管理员，不依赖真实机器数据）。"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, HERE)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import test_contract  # noqa: E402
import test_daemon  # noqa: E402
import test_ipc  # noqa: E402
import test_judgement  # noqa: E402
import test_kb  # noqa: E402
import test_l1l2  # noqa: E402
import test_l3  # noqa: E402
import test_llm  # noqa: E402
import test_web  # noqa: E402
from harness import run_module, summary  # noqa: E402


def main() -> int:
    print("AppGuard 测试套件（合成数据 + 协议契约 + 守护层回归 + 知识库 + Web 网关）")
    run_module("L1/L2 归属聚合与分类（合成雷电场景）", test_l1l2.run)
    run_module("L3 计划 / 风险分级 / 选择 / dry-run", test_l3.run)
    run_module("判定口径（在用标签 / 微软 / 服务 / 私自）", test_judgement.run)
    run_module("IPC 协议契约", test_ipc.run)
    run_module("IPC 契约（扁平结构 / 失效 / 选择 / limit）", test_contract.run)
    run_module("知识库闭环（pending / review / apply）", test_kb.run)
    run_module("LLM 解释（配置 / 文件 / Provider / 网关本地方法）", test_llm.run)
    run_module("Web 网关（HTTP / token / SSE / 安全）", test_web.run)
    run_module("守护层：持久化 / 降级 / 回收 / 契约校验", test_daemon.run)
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
