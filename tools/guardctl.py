"""guardctl 启动器（推荐入口）：把 `src/` 加进 sys.path 后调用 CLI。

用法示例：
    python tools/guardctl.py doctor
    python tools/guardctl.py software
    python tools/guardctl.py ps --explain
    python tools/guardctl.py plan
    python tools/guardctl.py clean --yes
    python tools/guardctl.py serve            # 启动守护（命名管道）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from appguard.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
