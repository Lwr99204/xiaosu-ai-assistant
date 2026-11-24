"""RAG 管道：加载解析 → 结构化分块 → 向量化存储 → 检索 → 引用定位（TDD §4.4）。

分层约束（TDD §3.1）：本包只依赖 app.core 与 app.config，**不 import app.tools**，
也不被 app.tools import。检索能力以异步可调用对象的形式由 services 层注入到
tools.knowledge，依赖方向始终是 services → rag / services → tools。
"""
