"""按配置构建 LLM 与 Embedding 提供者。

两个 Protocol 分开构建（CURRENT_PLAN 修正 5）：对话模型走公网 MaaS，向量化走
本地 BGE-M3 服务，两者的 base_url、超时、批量策略完全不同，合并成一个工厂方法
会把「换对话模型」和「换向量化后端」这两件互不相干的事耦在一起。

openai SDK 只在本模块被导入，llm/base.py 与 tokenizer.py 保持零第三方依赖，
所以 tools / agent / rag 引用它们时不会连带拉起客户端。
"""

from __future__ import annotations

from app.config import Settings
from app.llm.base import ChatProvider, EmbeddingProvider
from app.llm.mock import MockEmbedder, MockLLMProvider
from app.llm.openai_compat import OpenAICompatEmbedder, OpenAICompatProvider


def build_chat_provider(settings: Settings) -> ChatProvider:
    """构建对话提供者。llm_provider="mock" 用于测试与离线演示。"""
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    return OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        temperature=settings.llm_temperature,
    )


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """构建向量化提供者。

    dim 与已建索引强绑定：改了 embed_dim 必须重建向量库，否则写入时维度校验
    会直接抛 EmbeddingError（启动即失败，好过检索阶段静默返回错乱结果）。
    """
    if settings.llm_provider == "mock":
        return MockEmbedder(dim=settings.embed_dim)
    return OpenAICompatEmbedder(
        base_url=settings.embed_base_url,
        model=settings.embed_model,
        dim=settings.embed_dim,
        api_key=settings.embed_api_key,
        timeout_s=settings.embed_timeout_s,
        batch_size=settings.embed_batch_size,
        max_retries=settings.llm_max_retries,
    )
