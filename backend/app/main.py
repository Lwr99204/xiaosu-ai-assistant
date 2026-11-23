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

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import RequestContextMiddleware, install_exception_handlers
from app.config import Settings, get_settings
from app.logging_setup import setup_logging
from app.mock_api.repository import get_store
from app.mock_api.router import router as mock_router
from app.schemas.health import HealthComponent, HealthResponse, ImHealth, WecomHealth

logger = logging.getLogger(__name__)

APP_TITLE = "小苏 · 公司内部 AI 助手"

#: 与 backend/pyproject.toml 的 version 保持一致；健康检查与前端页脚共用
APP_VERSION = "0.1.0"

#: M1 阶段尚未接线的组件，统一回 not_wired（预期状态，不是故障）
_NOT_WIRED = "本阶段尚未接入"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动/关闭钩子。M3 起企业微信长连接在这里拉起与收尾。"""
    settings: Settings = app.state.settings
    logger.info(
        "小苏后端启动 version=%s mock_data_dir=%s log_dir=%s",
        APP_VERSION,
        settings.mock_data_dir,
        settings.log_dir,
    )
    logger.info("企业微信 IM enabled=%s", settings.wecom_enabled)
    yield
    logger.info("小苏后端关闭")


def build_health(settings: Settings) -> HealthResponse:
    """组装健康检查响应。

    后续里程碑把各组件换成真实探测（llm 探 base_url 可达性、embed 探 8001、
    vector_store 探 Chroma、db 探 SQLite、im.wecom 读长连接心跳时间），
    调用方与前端的契约不变。
    """
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        llm=HealthComponent(status="not_wired", detail=_NOT_WIRED),
        embed=HealthComponent(status="not_wired", detail=_NOT_WIRED),
        vector_store=HealthComponent(status="not_wired", detail=_NOT_WIRED),
        db=HealthComponent(status="not_wired", detail=_NOT_WIRED),
        im=ImHealth(
            wecom=WecomHealth(
                enabled=settings.wecom_enabled,
                connected=False,
                last_heartbeat=None,
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

    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)
    app.include_router(mock_router)

    @app.get("/api/health", response_model=HealthResponse, summary="健康检查")
    def health() -> HealthResponse:
        return build_health(resolved)

    return app
