"""FastAPI 应用工厂（TDD §5.1 / CURRENT_PLAN M1 第 4 项）。

用工厂函数而不是模块级 app 对象，有三个实际收益：
1. 测试可以传入自己的 Settings（临时 log_dir、假 API Key）构造隔离实例，
   不必改环境变量、也不会污染进程级 get_settings() 的缓存。
2. uvicorn 用 --factory 启动：uvicorn app.main:create_app --factory --reload
3. 启动顺序显式可见：先 setup_logging 再载入数据，任何启动期异常都能落进日志。

🔴 启动即载入并全量校验 mock 数据（repository 用 model_validate）：脏数据在进程
启动时就报错，符合 FR-6-02「启动即失败并说清原因」，而不是查询时静默漏行。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.orchestrator import build_orchestrator
from app.api.documents import router as document_router
from app.api.logs import router as log_router
from app.api.middleware import RequestContextMiddleware, install_exception_handlers
from app.api.settings import router as settings_router
from app.config import Settings, get_settings
from app.db.repositories import MessageRepo
from app.im.base import IMAdapter
from app.im.runner import IMRunner
from app.im.session import MessageDeduper
from app.im.wecom import WeComAdapter
from app.llm.factory import build_chat_provider
from app.logging_setup import setup_logging
from app.mock_api.repository import get_store
from app.mock_api.router import router as mock_router
from app.rag.pipeline import RAGPipeline, build_pipeline
from app.schemas.health import HealthComponent, HealthResponse, ImHealth, WecomHealth
from app.services.agent_service import AgentService
from app.services.document_service import DocumentService
from app.tools import build_tool_registry

logger = logging.getLogger(__name__)

APP_TITLE = "小苏 · 公司内部 AI 助手"

#: 与 backend/pyproject.toml 的 version 保持一致；健康检查与前端页脚共用
APP_VERSION = "0.1.0"

#: 尚未接线的组件统一回 not_wired（预期状态，不是故障）
_NOT_WIRED = "本阶段尚未接入"


def build_agent_service(
    settings: Settings,
    rag_pipeline: RAGPipeline | None = None,
) -> AgentService:
    """组装 Agent 的唯一入口，IM 与 Web API 共用（TDD §4.5.1）。

    RAG 就绪时仅注入异步检索函数；tools 不 import rag，依赖方向保持为组合根同时
    依赖两侧。RAG 启动失败时仍注册知识库工具，但明确返回「知识库当前不可用」。
    """
    knowledge_search = rag_pipeline.search_for_tool if rag_pipeline is not None else None
    registry = build_tool_registry(settings, knowledge_search=knowledge_search)
    orchestrator = build_orchestrator(settings, build_chat_provider(settings), registry)
    return AgentService(settings=settings, orchestrator=orchestrator)


def _log_im_task_result(task: asyncio.Task[None]) -> None:
    """长连接任务结束时记一条结果日志。

    不加这个回调，任务里抛出的异常只会变成「Task exception was never retrieved」
    警告，排查时看不到原因。取消属于正常关停，不当错误报。
    """
    if task.cancelled():
        logger.info("企业微信 IM 任务已取消")
        return
    error = task.exception()
    if error is not None:
        logger.error("企业微信 IM 任务异常退出 type=%s", type(error).__name__)


async def start_im(app: FastAPI, settings: Settings) -> WeComAdapter | None:
    """按配置拉起企业微信长连接；未启用或缺凭证时返回 None，不阻塞其他功能。"""
    if not (settings.wecom_enabled and settings.wecom_bot_id and settings.wecom_bot_secret):
        logger.info("企业微信 IM enabled=%s，跳过长连接", settings.wecom_enabled)
        return None
    adapter = WeComAdapter(settings)
    runner = IMRunner(
        settings=settings,
        service=app.state.agent_service,
        adapter=adapter,
        deduper=MessageDeduper(repo=MessageRepo(settings.sqlite_path)),
    )
    app.state.im_runner = runner
    task = asyncio.create_task(adapter.start(runner.handle), name="wecom-im")
    task.add_done_callback(_log_im_task_result)
    app.state.im_adapter = adapter
    app.state.im_task = task
    logger.info("企业微信 IM 长连接已拉起")
    return adapter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动/关闭钩子：Agent 服务 + SQLite 会话库 + 企业微信长连接。"""
    settings: Settings = app.state.settings
    logger.info(
        "小苏后端启动 version=%s mock_data_dir=%s log_dir=%s",
        APP_VERSION,
        settings.mock_data_dir,
        settings.log_dir,
    )
    rag_pipeline: RAGPipeline | None = build_pipeline(settings)
    try:
        vector_count = await rag_pipeline.verify_ready()
        app.state.rag_ready = True
        app.state.rag_detail = f"embedding 与 Chroma 正常，当前 {vector_count} 个分块"
        logger.info("RAG 管道就绪 vector_count=%d", vector_count)
    except Exception as exc:
        logger.error("RAG 管道启动检查失败 type=%s", type(exc).__name__)
        await rag_pipeline.close()
        rag_pipeline = None
        app.state.rag_ready = False
        app.state.rag_detail = "向量服务或索引不可用，请检查 127.0.0.1:8001 与 Chroma"
    app.state.rag_pipeline = rag_pipeline
    document_service = (
        DocumentService(settings=settings, pipeline=rag_pipeline) if rag_pipeline is not None else None
    )
    app.state.document_service = document_service
    app.state.agent_service = build_agent_service(settings, rag_pipeline)
    app.state.db_ready = True
    adapter = await start_im(app, settings)
    try:
        yield
    finally:
        if adapter is not None:
            await adapter.stop()
        if document_service is not None:
            await document_service.drain()
        if rag_pipeline is not None:
            await rag_pipeline.close()
        logger.info("小苏后端关闭")


def build_health(
    settings: Settings,
    *,
    db_ready: bool = False,
    adapter: IMAdapter | None = None,
    rag_ready: bool | None = None,
    rag_detail: str | None = None,
) -> HealthResponse:
    """组装健康检查响应。

    M3 起 db 与 im.wecom 是真实探测（SQLite 已建表、长连接心跳时间）。llm /
    embed / vector_store 仍回 not_wired：它们要到 M4 接本地向量服务与真实模型
    探测。响应字段保持不变，前端与评审脚本不用改。
    """
    wecom = adapter.health() if adapter is not None else None
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        llm=HealthComponent(status="not_wired", detail=_NOT_WIRED),
        embed=HealthComponent(
            status="not_wired" if rag_ready is None else ("ok" if rag_ready else "down"),
            detail=_NOT_WIRED if rag_ready is None else rag_detail,
        ),
        vector_store=HealthComponent(
            status="not_wired" if rag_ready is None else ("ok" if rag_ready else "down"),
            detail=_NOT_WIRED if rag_ready is None else rag_detail,
        ),
        db=HealthComponent(
            status="ok" if db_ready else "not_wired",
            detail=str(settings.sqlite_path) if db_ready else _NOT_WIRED,
        ),
        im=ImHealth(
            wecom=WecomHealth(
                enabled=settings.wecom_enabled,
                connected=bool(wecom is not None and wecom.connected),
                last_heartbeat=wecom.last_heartbeat if wecom is not None else None,
            )
        ),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造 FastAPI 实例。参数留空时读进程级配置单例。"""
    resolved = settings or get_settings()
    # 必须最先执行：后面任何启动期日志（含数据校验失败的异常栈）才能落盘
    setup_logging(resolved.log_dir)

    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        lifespan=lifespan,
        summary="公司内部 AI 助手后端：Mock 内部 API、问答、知识库、对话日志",
    )
    app.state.settings = resolved
    app.state.store = get_store(resolved.mock_data_dir)
    app.state.document_service = None

    def switch_model(model_id: str) -> None:
        resolved.llm_model = model_id
        service = build_agent_service(resolved, getattr(app.state, "rag_pipeline", None))
        app.state.agent_service = service
        runner = getattr(app.state, "im_runner", None)
        if runner is not None:
            runner.replace_service(service)
        logger.info("对话模型已切换 model=%s", model_id)

    app.state.switch_model = switch_model

    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)
    app.include_router(mock_router)
    app.include_router(document_router)
    app.include_router(log_router)
    app.include_router(settings_router)

    @app.get("/api/health", response_model=HealthResponse, summary="健康检查")
    def health() -> HealthResponse:
        return build_health(
            resolved,
            db_ready=bool(getattr(app.state, "db_ready", False)),
            adapter=getattr(app.state, "im_adapter", None),
            rag_ready=getattr(app.state, "rag_ready", None),
            rag_detail=getattr(app.state, "rag_detail", None),
        )

    return app


# 兼容部署文档中的 ``uvicorn app.main:app``，同时保持模块导入时不读取真实 .env。
# 首个 ASGI lifespan 调用才构造 FastAPI，因此单测仍可显式 create_app(test_settings)。
_runtime_app: FastAPI | None = None


async def app(scope, receive, send) -> None:
    global _runtime_app
    if _runtime_app is None:
        _runtime_app = create_app()
    await _runtime_app(scope, receive, send)
