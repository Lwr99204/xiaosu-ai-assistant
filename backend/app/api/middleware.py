"""HTTP 中间件与全局异常兜底（TDD §4.2 / §4.9 / FR-6-07「永不裸 500」）。

三件事集中在这里，路由层因此可以只做协议转换
--------------------------------------------
1. request_id：优先透传调用方给的 X-Request-ID（前端重试同一问题时能串起整条
   链路），否则自己生成；绑定到 core.ids 的 ContextVar，日志行与错误响应体共用
   同一个值——用户拿着响应里的 request_id 就能直接 grep logs/app.log。
2. 耗时：core.timing.timed() 统计每个请求，慢于阈值单独打 WARNING。
3. 异常兜底：AppError 走统一错误体；Pydantic 校验失败转 422 + 字段级说明
   （FR-3-07，字段级信息也是给模型自纠用的）；其余一切转 500 INTERNAL_ERROR，
   既不让 FastAPI 的裸 500 出去，也不把异常栈回给用户（NFR-11）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.errors import AppError, ValidationError
from app.core.ids import bind_request_id, current_request_id, unbind_request_id
from app.core.timing import timed

logger = logging.getLogger(__name__)

#: 全链路追踪头。请求可带入、响应必回写（FR-6-06）
REQUEST_ID_HEADER = "X-Request-ID"

#: 慢请求阈值（毫秒）。超过就升级到 WARNING，便于从日志里 grep 出瓶颈
SLOW_REQUEST_MS = 3000.0

#: 未捕获异常兜底错误码（区别于业务侧显式抛出的 AppError）
UNEXPECTED_ERROR_CODE = "INTERNAL_ERROR"

#: 只接受这个形状的透传 id。放任调用方传任意字符串等于开放日志注入面
#: （一个换行符就能往 logs/app.log 里塞一条假日志）
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def request_id_of(request: Request) -> str:
    """读取本次请求的 request_id。

    优先取中间件写进 request.state 的值，退化时才读 ContextVar：未捕获异常由最
    外层 ServerErrorMiddleware 处理，那时中间件的上下文早已还原，只有挂在共享
    scope 上的 request.state 还能拿到正确的 id。
    """
    stored = getattr(request.state, "request_id", None)
    return stored or current_request_id()


def _error_response(request: Request, exc: AppError) -> JSONResponse:
    """把 AppError 渲染成统一错误响应（响应体与响应头都带 request_id）。"""
    request_id = request_id_of(request)
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_response_dict(request_id),
        headers={REQUEST_ID_HEADER: request_id},
    )


def _field_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """把 pydantic 的校验错误压成「字段 -> 人话」列表。

    loc 第 0 段是位置（query/body/path），对调用方没价值，去掉只留字段路径。
    """
    result: list[dict[str, Any]] = []
    for raw in exc.errors():
        loc = raw.get("loc", ())
        field = ".".join(str(part) for part in loc[1:])
        result.append(
            {
                "field": field or str(loc[0] if loc else "request"),
                "message": raw.get("msg", "参数非法"),
                "type": raw.get("type", ""),
            }
        )
    return result


class RequestContextMiddleware(BaseHTTPMiddleware):
    """绑定 request_id，并记录访问日志与耗时。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id, token = bind_request_id(
            incoming if incoming and _REQUEST_ID_PATTERN.match(incoming) else None
        )
        # 同时写进 state：异常处理链在上下文还原之后仍要能拿到它
        request.state.request_id = request_id
        try:
            with timed() as timer:
                response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            self._log_access(request, response.status_code, timer.elapsed_ms or 0.0)
            return response
        finally:
            unbind_request_id(token)

    @staticmethod
    def _log_access(request: Request, status_code: int, elapsed_ms: float) -> None:
        """访问日志。只记 path 不记 query：query 里将来会出现文件名等用户输入，
        而排查靠 request_id 关联业务日志已经足够（NFR-11 少一处泄露面）。
        """
        slow = elapsed_ms >= SLOW_REQUEST_MS
        level = logging.WARNING if slow or status_code >= 500 else logging.INFO
        logger.log(
            level,
            "%s %s -> %d 耗时 %.1fms%s",
            request.method,
            request.url.path,
            status_code,
            elapsed_ms,
            "（慢请求）" if slow else "",
        )


def install_exception_handlers(app: FastAPI) -> None:
    """装全局异常处理器（TDD §4.9：统一在 middleware.py 收口）。"""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 5xx 是「我们出了问题」，进 logs/error.log；4xx 是「调用方给错了」，
        # INFO 足够——否则用户手滑一次就污染错误日志
        if exc.http_status >= 500:
            logger.error("AppError %s: %s context=%s", exc.code, exc.message, exc.context)
        else:
            logger.info("AppError %s: %s", exc.code, exc.message)
        return _error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = _field_errors(exc)
        logger.info("入参校验失败 path=%s errors=%s", request.url.path, errors)
        return _error_response(request, ValidationError("入参校验失败", errors=errors))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # logger.exception 会带完整栈，栈里的文本经 RedactingFormatter 脱敏后才落盘
        logger.exception("未捕获异常 path=%s", request.url.path)
        return _error_response(
            request,
            AppError("服务内部错误", code=UNEXPECTED_ERROR_CODE, cause=exc),
        )
