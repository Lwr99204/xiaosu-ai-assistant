"""工具注册中心。

模型通过 function calling 自主决定调用哪个工具，本模块是唯一的对接点：把工具
定义翻译成模型能读的 schema，以及把模型的工具请求安全地执行掉。

invoke() 内部**不向上层抛任何异常**（FR-3-07/08）。工具失败不该升级成 500，
而应变成一段 role="tool" 的文本回灌给模型，让它自己决定重试还是换工具——这是
「调用稳定」的关键。四类失败分别给出不同粒度的错误：未知工具名、参数不是合法
JSON、参数校验不过（**逐字段**回传，模型能据此补齐后自纠）、执行超时或抛异常。

新增工具只需两步（FR-3-11，服务面试现场改代码）：
1. 在 app/tools/ 下写一个文件，导出一个 ToolSpec
2. 在 app/tools/__init__.py 的 ALL_SPECS 里加一行
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.timing import start_timer

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT_S = 10.0

# 单个工具回灌给模型的文本上限。工具结果会进上下文窗口，一条订单列表若不加限制
# 就能吃掉几千 token，把后面的对话历史挤掉。
_MAX_OUTPUT_CHARS = 4000
_MAX_ERROR_DETAIL_CHARS = 200


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个工具的定义。description 是模型判断「该不该用」的唯一依据，措辞至关重要。"""

    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[..., Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """一次工具执行的结果。output 原样回灌给模型，latency_ms 落库供后台展示工具链。"""

    ok: bool
    output: str
    tool_name: str
    latency_ms: float
    error: str | None = None


def _strip_titles(node: Any) -> Any:
    """递归去掉 Pydantic 生成的 JSON Schema 里的 title。

    title 只是类名的驼峰形式（EmployeeQueryArgs），对模型选工具毫无帮助，而部分
    OpenAI 兼容网关会把它原样透传给底层模型，白占 token。
    """
    if isinstance(node, dict):
        return {key: _strip_titles(value) for key, value in node.items() if key != "title"}
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    return node


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + f"...(内容已截断，原始长度 {len(text)} 字符)"


def _field_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Pydantic 校验错误 → 字段级错误列表。

    只取 loc / msg / type 三个字段：errors() 里的 ctx 可能含 datetime、Path 等
    不可 JSON 序列化对象，直接塞进去会让回灌文本生成失败。
    """
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]) or "<root>",
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]


class ToolRegistry:
    """工具表。同名工具后注册覆盖先注册，便于测试注入桩工具。"""

    def __init__(self, *, timeout_s: float = DEFAULT_TOOL_TIMEOUT_S) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._timeout_s = timeout_s

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def register_all(self, specs: Sequence[ToolSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs)

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """转成 OpenAI function calling 格式。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": _strip_titles(spec.parameters.model_json_schema()),
                },
            }
            for spec in self._specs.values()
        ]

    def _fail(self, name: str, latency_ms: float, error: str, **extra: Any) -> ToolResult:
        payload = {"ok": False, "error": error, **extra}
        logger.warning("tool call failed: %s (%s)", name, error)
        dumped = json.dumps(payload, ensure_ascii=False, default=str)
        return ToolResult(
            ok=False,
            output=_clip(dumped),
            tool_name=name,
            latency_ms=latency_ms,
            error=error,
        )

    async def invoke(self, name: str, raw_arguments: str) -> ToolResult:
        """执行一次工具调用。四步中任一步失败都返回 ToolResult，不抛异常。"""
        timer = start_timer()
        spec = self._specs.get(name)
        if spec is None:
            return self._fail(name, timer.stop(), f"unknown tool: {name}")

        try:
            raw = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return self._fail(
                name,
                timer.stop(),
                "arguments 不是合法 JSON，请重新生成",
                detail=str(exc)[:_MAX_ERROR_DETAIL_CHARS],
            )

        if not isinstance(raw, Mapping):
            return self._fail(
                name,
                timer.stop(),
                "arguments 必须是 JSON 对象",
                got=type(raw).__name__,
            )

        try:
            arguments = spec.parameters.model_validate(dict(raw))
        except ValidationError as exc:
            return self._fail(
                name,
                timer.stop(),
                "arguments 校验失败，请按 errors 修正后重试",
                errors=_field_errors(exc),
            )

        try:
            output = await asyncio.wait_for(
                spec.handler(**arguments.model_dump()),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            return self._fail(name, timer.stop(), f"tool 执行超过 {self._timeout_s}s 已中断")
        except Exception as exc:
            logger.exception("tool handler raised: %s", name)
            return self._fail(
                name,
                timer.stop(),
                f"tool 执行出错：{type(exc).__name__}",
                detail=str(exc)[:_MAX_ERROR_DETAIL_CHARS],
            )

        return ToolResult(ok=True, output=_clip(output), tool_name=name, latency_ms=timer.stop())
