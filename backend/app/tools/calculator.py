"""calculator 工具：AST 白名单求值四则运算。

为什么不用 eval()：expression 是 LLM 生成的字符串，eval("__import__('os').system(...)")
会真的执行。这里改成先 ast.parse 再遍历语法树，只放行「数字字面量、四则与幂/取模、
一元正负号、少量数值函数、pi/e 常量」，遇到白名单外节点（Name 查未知标识符、
Attribute 属性访问、Subscript 下标、Lambda、推导式等）一律拒绝。

description 里那句「禁止心算」是刻意写的（TDD §4.5.2）：验收题 7.2-5「上周一共多少
订单」和题面「上周三的销售额是多少」都要求 Agent 自己汇总，LLM 心算一列金额极易出错。
"""

from __future__ import annotations

import ast
import json
import math
import operator
from collections.abc import Callable

from pydantic import BaseModel, Field

from app.agent.tool_registry import ToolSpec

_MAX_EXPRESSION_CHARS = 200
# 9**9**9 这类表达式会把内存吃满也等不到 OverflowError，所以先卡住操作数量级。
_MAX_OPERAND = 1e15
_MAX_EXPONENT = 64.0
# 语法树节点上限，挡住解析炸弹。
_MAX_NODES = 200

_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 只收数值函数。刻意不给 sum：模型容易写成 sum([1,2,3])，而列表不在白名单里会被拒；
# 汇总求和写成 1+2+3 语义等价且没有额外攻击面。
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "min": min,
    "max": max,
}

_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}


class CalculatorError(ValueError):
    """表达式非法或计算失败。会被 handler 转成错误文本回灌给模型。"""


class CalculatorArgs(BaseModel):
    """calculator 入参。"""

    expression: str = Field(
        min_length=1,
        max_length=_MAX_EXPRESSION_CHARS,
        description="要计算的算式，例如 15600+3000+98000 或 (121400-790)*0.13",
    )


def _number(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        # bool 是 int 的子类，True+1 这种要挡掉，否则模型会以为支持布尔逻辑
        if isinstance(node.value, bool):
            raise CalculatorError("只允许数字字面量，不允许布尔值")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        return _binary(node)
    if isinstance(node, ast.UnaryOp):
        return _unary(node)
    if isinstance(node, ast.Call):
        return _call(node)
    if isinstance(node, ast.Name):
        return _constant(node.id)
    raise CalculatorError(f"不支持的语法：{type(node).__name__}")


def _safe_pow(base: float, exponent: float) -> float:
    if abs(base) > _MAX_OPERAND or abs(exponent) > _MAX_EXPONENT:
        raise CalculatorError(
            f"幂运算超出安全范围（底数不超过 {_MAX_OPERAND:g}，指数不超过 {_MAX_EXPONENT:g}）"
        )
    try:
        return float(base**exponent)
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        raise CalculatorError(f"幂运算失败：{exc}") from exc


def _binary(node: ast.BinOp) -> float:
    operation = _BINARY_OPERATORS.get(type(node.op))
    if operation is None:
        if isinstance(node.op, ast.Pow):
            return _safe_pow(_number(node.left), _number(node.right))
        raise CalculatorError(f"不支持的运算符：{type(node.op).__name__}")
    left = _number(node.left)
    right = _number(node.right)
    try:
        return float(operation(left, right))
    except ZeroDivisionError as exc:
        raise CalculatorError("除数为零") from exc
    except (OverflowError, ValueError) as exc:
        raise CalculatorError(f"结果超出可表示范围：{exc}") from exc


def _unary(node: ast.UnaryOp) -> float:
    operation = _UNARY_OPERATORS.get(type(node.op))
    if operation is None:
        raise CalculatorError(f"不支持的一元运算符：{type(node.op).__name__}")
    return float(operation(_number(node.operand)))


def _call(node: ast.Call) -> float:
    if node.keywords or not isinstance(node.func, ast.Name):
        raise CalculatorError("只支持按函数名直接调用，不支持关键字参数与属性调用")
    function = _FUNCTIONS.get(node.func.id)
    if function is None:
        raise CalculatorError(f"不支持的函数：{node.func.id}")
    arguments = [_number(argument) for argument in node.args]
    try:
        return float(function(*arguments))
    except (ValueError, OverflowError, ZeroDivisionError, TypeError) as exc:
        raise CalculatorError(f"{node.func.id} 计算失败：{exc}") from exc


def _constant(name: str) -> float:
    value = _CONSTANTS.get(name)
    if value is None:
        allowed = "/".join(sorted(_CONSTANTS))
        raise CalculatorError(f"未知标识符：{name}（只允许常量 {allowed}）")
    return value


def evaluate(expression: str) -> float:
    """求值一个算式。任何非法语法或越界结果都抛 CalculatorError。"""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"表达式语法错误：{exc.msg}") from exc
    except (RecursionError, MemoryError) as exc:
        raise CalculatorError("表达式嵌套过深") from exc

    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise CalculatorError(f"表达式过于复杂，最多 {_MAX_NODES} 个语法节点")

    try:
        return _number(tree.body)
    except RecursionError as exc:
        raise CalculatorError("表达式嵌套过深") from exc


def _tidy(value: float) -> int | float:
    """整数值去掉 .0，免得模型把 121400.0 当成异常精度再解释一遍。"""
    if value.is_integer() and abs(value) < _MAX_OPERAND:
        return int(value)
    return value


async def run(expression: str) -> str:
    """工具 handler。失败也返回 ok=false 的 JSON，让模型看到原因后自纠。"""
    try:
        value = evaluate(expression)
    except CalculatorError as exc:
        return json.dumps(
            {"ok": False, "expression": expression, "error": str(exc)},
            ensure_ascii=False,
        )
    return json.dumps(
        {"ok": True, "expression": expression, "result": _tidy(value)},
        ensure_ascii=False,
    )


DESCRIPTION = (
    "执行四则运算、百分比换算、汇总求和，返回 JSON 格式的计算结果。"
    "凡涉及金额、天数、数量的求和或换算必须调用本工具，禁止心算。"
)

SPEC = ToolSpec(
    name="calculator",
    description=DESCRIPTION,
    parameters=CalculatorArgs,
    handler=run,
)
