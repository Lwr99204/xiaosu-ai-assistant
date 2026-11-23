"""Agent 编排层。

本包不 import app.tools 的任何具体实现：编排层只跟 ToolRegistry 契约打交道，
工具集的装配发生在 services 层（TDD §3.1）。因此 app.tools 依赖
agent.tool_registry 里的 ToolSpec / ToolRegistry 不构成环。

刻意不在这里 re-export orchestrator：orchestrator 会连带导入 llm 层，
而 tool_registry / prompts 是纯本地定义，测试与工具层都能轻量引用。
"""
