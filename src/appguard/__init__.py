"""AppGuard —— 未在用软件的后台组件治理工具（核心/后端）。

三条主线：
  L1 inventory  展示当前在用哪些软件
  L2 classify   解释每个进程是干嘛的（对任务管理器做分类）
  L3 cleanup    一键清理"未在用软件私自开启的组件/残留进程"

本包**不含任何 UI**：所有对外能力经 IPC（见 接口规范.md）暴露。
"""

__version__ = "0.4.0"
API_VERSION = 1

__all__ = ["__version__", "API_VERSION"]
