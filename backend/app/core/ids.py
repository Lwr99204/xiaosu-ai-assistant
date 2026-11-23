"""request_id / 会话 id 生成与跨异步透传（TDD §4.2 / FR-6-06）。

为什么用 `contextvars.ContextVar` 而不是把 request_id 层层透传到每个函数签名：
FastAPI 的请求处理、后台任务、日志打点分散在多层，手动透传会污染所有函数签名，
且异步并发下极易串号。ContextVar 在 asyncio 中**每个 Task 自动拷贝上下文**，
天然做到「同一请求内处处可读、不同请求之间互不干扰」。

🔴 已知坑：`asyncio.create_task()` 会拷贝**创建时刻**的上下文，所以在任务里改
ContextVar 不会影响外层；反过来，若在外层 set 之后再 create_task，子任务能读到。
后台索引任务（M4）必须遵守「先 bind 再 create_task」的顺序，否则日志会丢 request_id。
"""

from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar, Token

#: 未绑定请求上下文时的占位值。用 `-` 而非空串，是为了让日志列宽稳定、
#: 且一眼能看出「这条日志不属于任何 HTTP 请求」（如启动期日志、后台任务漏绑定）
NO_REQUEST_ID = "-"

_request_id_var: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)


def new_id(prefix: str = "", length: int = 12) -> str:
    """生成带前缀的短 id。

    用 uuid4 的前 `length` 位十六进制（48 bit 熵）：本项目是单机内部系统，
    碰撞概率可忽略，而短 id 在日志、URL、企业微信消息里都更好读好复制。
    """
    raw = uuid.uuid4().hex[:length]
    return f"{prefix}_{raw}" if prefix else raw


def new_request_id() -> str:
    """请求级追踪 id，形如 `req_8f3a1c2b9d04`（TDD §5.1 示例格式）。"""
    return new_id("req")


def new_conversation_id() -> str:
    """对话记录主键（M2 起用于 conversations 表）。"""
    return new_id("conv")


def new_doc_id() -> str:
    """文档 id（M2 起用于 documents 表）。"""
    return new_id("doc")


def current_request_id() -> str:
    """读取当前上下文的 request_id；未绑定时返回 `-`，**绝不返回 None**——
    否则日志格式串会打出 `None` 字样，看起来像 bug。
    """
    return _request_id_var.get()


def bind_request_id(request_id: str | None = None) -> tuple[str, Token[str]]:
    """绑定 request_id 到当前上下文，返回 (实际使用的 id, Token)。

    Token 必须传给 `unbind_request_id` 还原，否则在复用线程/上下文的场景
    （如 uvicorn 的 worker 循环、pytest 同一进程跑多条用例）会串号。
    """
    rid = request_id or new_request_id()
    token = _request_id_var.set(rid)
    return rid, token


def unbind_request_id(token: Token[str]) -> None:
    """还原上下文。对已失效的 Token 静默忽略，避免中间件在异常路径二次抛错。"""
    # 跨上下文 reset 会抛 ValueError（Token 来自别的 Context），属可预期情况
    with contextlib.suppress(ValueError, LookupError):
        _request_id_var.reset(token)
