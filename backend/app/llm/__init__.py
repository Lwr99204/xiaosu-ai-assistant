"""LLM 适配层。

本包刻意**不在 __init__ 里 re-export 任何实现**：openai_compat 会在导入时拉起
openai SDK，而 app.llm.base / tokenizer 是纯数据结构与本地计算，被 tools、agent、
rag 广泛引用。让它们保持轻量导入，测试才能在 mock provider 下完全不碰第三方客户端。

需要真实 provider 时走 app.llm.factory。
"""
