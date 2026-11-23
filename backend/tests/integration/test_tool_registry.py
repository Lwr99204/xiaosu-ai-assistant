"""ToolRegistry 单元测试：四类失败全部返回 ToolResult 而非抛异常（FR-3-07/08）。

注册中心的契约是「工具失败降级为一段回灌文本」，任何异常升级成 500 都会让
验收题 Q10 的友好降级失守。这里逐类验证：未知工具、非法 JSON、非对象 JSON、
参数校验失败（逐字段）、超时、handler 抛异常，外加输出截断与 schema 清洁度。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from pydantic import BaseModel, Field

from app.agent.tool_registry import ToolRegistry, ToolSpec

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


class _EchoArgs(BaseModel):
    value: str = Field(min_length=1, max_length=8)
    count: int = Field(default=1, ge=1, le=3)


async def _echo(value: str, count: int = 1) -> str:
    return json.dumps({"ok": True, "echo": value * count}, ensure_ascii=False)


class _DateArgs(BaseModel):
    start: date


async def _date_handler(start: date) -> str:
    return json.dumps({"ok": True, "type": type(start).__name__, "iso": start.isoformat()})


def _registry(timeout_s: float = 1.0) -> ToolRegistry:
    registry = ToolRegistry(timeout_s=timeout_s)
    registry.register(
        ToolSpec(name="echo", description="回显文本", parameters=_EchoArgs, handler=_echo)
    )
    return registry


async def test_happy_path_returns_ok_result():
    result = await _registry().invoke("echo", '{"value": "小苏小苏"}')
    assert result.ok is True
    assert result.error is None
    assert result.tool_name == "echo"
    assert result.latency_ms >= 0
    assert json.loads(result.output) == {"ok": True, "echo": "小苏小苏"}


async def test_unknown_tool():
    result = await _registry().invoke("nope", "{}")
    assert result.ok is False
    assert result.error == "unknown tool: nope"


async def test_invalid_json_arguments():
    result = await _registry().invoke("echo", '{"value": ')
    assert result.ok is False
    assert result.error == "arguments 不是合法 JSON，请重新生成"


async def test_non_object_json_arguments():
    result = await _registry().invoke("echo", "[1, 2]")
    assert result.ok is False
    assert result.error == "arguments 必须是 JSON 对象"
    assert json.loads(result.output)["got"] == "list"


async def test_validation_error_reports_field_details():
    result = await _registry().invoke("echo", '{"value": "这个字符串超过了八个字符的上限"}')
    assert result.ok is False
    assert result.error == "arguments 校验失败，请按 errors 修正后重试"
    payload = json.loads(result.output)
    assert payload["errors"][0]["field"] == "value"
    assert payload["errors"][0]["type"] == "string_too_long"


async def test_handler_timeout_is_captured():
    async def _slow(**kwargs) -> str:
        await asyncio.sleep(5)
        return "never"

    registry = ToolRegistry(timeout_s=0.05)
    registry.register(
        ToolSpec(name="slow", description="慢工具", parameters=_EchoArgs, handler=_slow)
    )
    result = await registry.invoke("slow", '{"value": "x"}')
    assert result.ok is False
    assert "超过" in (result.error or "")


async def test_handler_exception_becomes_tool_result():
    async def _boom(**kwargs) -> str:
        raise RuntimeError("secret internal detail")

    registry = _registry()
    registry.register(
        ToolSpec(name="boom", description="会炸", parameters=_EchoArgs, handler=_boom)
    )
    result = await registry.invoke("boom", '{"value": "x"}')
    assert result.ok is False
    assert result.error == "tool 执行出错：RuntimeError"
    assert "Traceback" not in result.output


async def test_oversized_output_is_clipped():
    async def _big(**kwargs) -> str:
        return "长" * 9000

    registry = _registry()
    registry.register(
        ToolSpec(name="big", description="大输出", parameters=_EchoArgs, handler=_big)
    )
    result = await registry.invoke("big", '{"value": "x"}')
    assert result.ok is True
    assert "内容已截断" in result.output
    assert "9000" in result.output
    assert len(result.output) < 9000


async def test_date_argument_reaches_handler_as_date_object():
    registry = _registry()
    registry.register(
        ToolSpec(name="when", description="日期", parameters=_DateArgs, handler=_date_handler)
    )
    result = await registry.invoke("when", '{"start": "2026-11-16"}')
    assert result.ok is True
    payload = json.loads(result.output)
    assert payload["type"] == "date"
    assert payload["iso"] == date(2026, 11, 16).isoformat()


async def test_openai_schema_is_json_serializable_and_title_free():
    registry = _registry()
    tools = registry.to_openai_tools()
    dumped = json.dumps(tools, ensure_ascii=False)
    assert "title" not in dumped
    spec = tools[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "echo"
    assert spec["function"]["parameters"]["required"] == ["value"]


async def test_same_name_registration_overrides():
    async def _echo2(value: str, count: int = 1) -> str:
        return json.dumps({"ok": True, "echo": "v2:" + value}, ensure_ascii=False)

    registry = _registry()
    registry.register(ToolSpec(name="echo", description="v2", parameters=_EchoArgs, handler=_echo2))
    result = await registry.invoke("echo", '{"value": "a"}')
    assert json.loads(result.output)["echo"] == "v2:a"
