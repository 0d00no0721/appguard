"""`python -m appguard` 入口（等价于 guardctl）。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
