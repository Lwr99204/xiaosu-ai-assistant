"""工具集合。

新增一个工具只需两步（FR-3-11，服务面试现场改代码）：
1. 在本目录新建一个文件，导出模块级 SPEC；若工具需要依赖注入则导出 build_* 工厂
2. 无依赖的工具在 ALL_SPECS 里加一行，需要注入的在 build_all_specs 里加一行

依赖方向（TDD §3.1）：tools 不 import rag / llm，检索器由 services 层注入。
对 agent.tool_registry 的单向依赖是刻意的——ToolSpec 是工具的通用契约，
定义在注册中心所在处，而 agent 编排层反过来不 import tools 的任何具体实现，
两者只在 services 层组合，因此不构成环。
"""

from __future__ import annotations

from app.agent.tool_registry import DEFAULT_TOOL_TIMEOUT_S, ToolRegistry, ToolSpec
from app.config import Settings
from app.mock_api.repository import get_store
from app.tools import calculator, clock, internal_api, knowledge
from app.tools.knowledge import KnowledgeSearchFn

# 无需任何注入的工具。新增通用工具就是「写一个文件 + 在这里加一行」。
ALL_SPECS: tuple[ToolSpec, ...] = (calculator.SPEC, clock.SPEC)


def build_all_specs(
    settings: Settings,
    *,
    knowledge_search: KnowledgeSearchFn | None = None,
) -> list[ToolSpec]:
    """汇总全部工具。

    knowledge_search 为 None 时 search_knowledge_base 仍会注册，但返回「知识库当前
    不可用」。保持工具清单在各里程碑之间稳定，模型行为才不会因为少一个工具而漂移。
    """
    store = get_store(settings.mock_data_dir)
    return [
        *ALL_SPECS,
        *internal_api.build_specs(store),
        knowledge.build_spec(knowledge_search, top_k=settings.retrieval_top_k),
    ]


def build_tool_registry(
    settings: Settings,
    *,
    knowledge_search: KnowledgeSearchFn | None = None,
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
) -> ToolRegistry:
    """构建装配好全部工具的注册中心，供编排层使用。"""
    registry = ToolRegistry(timeout_s=timeout_s)
    registry.register_all(build_all_specs(settings, knowledge_search=knowledge_search))
    return registry
