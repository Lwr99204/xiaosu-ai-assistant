"""calculator 工具单测：AST 白名单求值的正确性与安全性（TDD §4.5.2 / FR-3-06）。

expression 是 LLM 生成的字符串，等于把一段不可信代码交给后端执行。这里断言两件
事：该算对的算对（含验收题 Q5 的真实金额汇总），以及白名单外的任何写法都被拒。
"""

from __future__ import annotations

import json

import pytest

from app.tools.calculator import CalculatorError, evaluate, run

#: Windows 上 asyncio 事件循环要用 socketpair() 建 self-pipe，与 pyproject 里的
#: 全局 --disable-socket 冲突。挂 allow_hosts 后 pytest-socket 改守 connect()，
#: 白名单外的主机仍然抛 SocketConnectBlockedError，禁网约束不变。
pytestmark = pytest.mark.allow_hosts("127.0.0.1")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+2*3", 7),
        ("(1+2)*3", 9),
        ("10/4", 2.5),
        ("10//4", 2),
        ("10%3", 1),
        ("-5+3", -2),
        ("2**10", 1024),
        ("sqrt(16)", 4),
        ("abs(-3)", 3),
        ("round(2.5)", 2),
        ("ceil(1.2)", 2),
        ("floor(1.8)", 1),
        ("max(1,5,3)", 5),
        ("min(1,5,3)", 1),
        ("3.14*2", 6.28),
    ],
)
def test_evaluate_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression) == pytest.approx(expected)


def test_evaluate_sums_last_week_paid_orders() -> None:
    """验收题 Q5 的真实算式：上周已支付订单金额汇总应为 121400。

    这条断言同时锁住 data/mock 的口径——退款 790 与取消 1580 不计入销售额。
    """
    assert evaluate("15600+3000+98000+4200+600") == pytest.approx(121400)


def test_evaluate_percentage_conversion() -> None:
    assert evaluate("(121400-790)*0.13") == pytest.approx(15679.3)


def test_evaluate_supports_whitelisted_constants() -> None:
    assert evaluate("round(pi*100)") == pytest.approx(314)


@pytest.mark.parametrize(
    "expression",
    [
        "9**9**9",
        "2**65",
        "__import__('os').system('x')",
        "open('/etc/passwd').read()",
        "(1).__class__",
        "[1,2][0]",
        "lambda: 1",
        "x+1",
        "sum([1,2])",
        "True+1",
        "1/0",
        "1+",
    ],
)
def test_evaluate_rejects_unsafe_or_invalid(expression: str) -> None:
    """解析炸弹、代码执行、属性内省、白名单外名字与语法错误一律拒绝。"""
    with pytest.raises(CalculatorError):
        evaluate(expression)


def test_evaluate_rejects_expression_with_too_many_nodes() -> None:
    """超长算式在解析阶段就被节点数上限挡掉，不进求值。"""
    with pytest.raises(CalculatorError, match="过于复杂"):
        evaluate("1+" * 300 + "1")


async def test_run_returns_ok_payload() -> None:
    payload = json.loads(await run("15600+3000"))

    assert payload == {"ok": True, "expression": "15600+3000", "result": 18600}


async def test_run_strips_trailing_zero_for_integer_results() -> None:
    """整数值去掉 .0，免得模型把 121400.0 当成异常精度再解释一遍。"""
    payload = json.loads(await run("60700*2"))

    assert payload["result"] == 121400
    assert isinstance(payload["result"], int)


async def test_run_returns_error_payload_instead_of_raising() -> None:
    """工具失败也返回 ok=false 的 JSON，让模型看到原因后自纠（FR-3-08）。"""
    payload = json.loads(await run("1/0"))

    assert payload["ok"] is False
    assert payload["expression"] == "1/0"
    assert "零" in payload["error"]
