"""小苏 · 公司内部 AI 助手 — 后端应用包。

分层依赖（TDD §3.1，单向，禁止反向）：
    api / im ──► services ──► agent / rag / tools / llm / db
                                      └──► core / config / schemas（叶子层）
    mock_api ──► 独立子域，仅依赖自身 schemas
"""
