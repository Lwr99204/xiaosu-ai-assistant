"""内部 Mock API（题目功能3 的工具 A，FR-3-02）。

独立子域：只依赖本包的 `schemas.py`，不 import services / agent / db。
作为 FastAPI 子路由挂在同一应用内，而非独立进程（TDD §4.8）——
少一个进程就少一处启动失败点，这对「一条命令启动」（FR-6-01）很关键。
"""
