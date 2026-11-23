"""内部系统 API 工具：员工、考勤、订单。

复用 app.mock_api.repository 的 MockDataStore，而不是另起一份 JSON 解析：Mock API 与
工具必须看到完全相同的数据与过滤语义，否则同一道验收题走 HTTP 和走 Agent 会给出不同
答案，这类不一致最难排查。

query_orders 刻意**只返回明细、不返回任何汇总数字**（CURRENT_PLAN 修正 3）：验收题
7.2-5「上周一共多少订单」与题面「上周三的销售额是多少」都要求 Agent 先调 query_orders
再调 calculator 自己算。这里若顺手把 count 或 total_amount 算好返回，等于把题目要考的
「模型自主编排两个工具」抹掉，属于拿接口预计算冒充 Agent 能力。

同理 query_attendance 不返回 status_counts：验收题 7.3-7「他上周来上班几天」的答案正是
按状态筛选后计数，预先给出分布等于直接给答案。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.agent.tool_registry import ToolSpec
from app.mock_api.repository import MockDataStore
from app.mock_api.schemas import OrderStatus

# 单次查询订单过多时结果会撑爆上下文窗口并被注册中心截断，模型将基于残缺数据计数。
# 与其静默给错答案，不如明确要求缩小日期区间。
_MAX_ORDERS_PER_CALL = 200


class EmployeeArgs(BaseModel):
    """query_employee 入参。"""

    emp_id: str = Field(min_length=1, max_length=32, description="员工工号，三位数字，例如 001")


class AttendanceArgs(BaseModel):
    """query_attendance 入参。"""

    emp_id: str = Field(min_length=1, max_length=32, description="员工工号，例如 001")
    start_date: date = Field(description="起始日期（含），ISO 格式，例如 2026-11-16")
    end_date: date = Field(description="结束日期（含），ISO 格式，例如 2026-11-22")


class OrderArgs(BaseModel):
    """query_orders 入参。"""

    start_date: date = Field(description="起始日期（含），ISO 格式，例如 2026-11-16")
    end_date: date = Field(description="结束日期（含），ISO 格式，例如 2026-11-22")
    customer: str | None = Field(
        default=None, max_length=64, description="客户名称，模糊匹配；不填表示全部客户"
    )
    status: OrderStatus | None = Field(default=None, description="订单状态筛选；不填表示全部状态")


EMPLOYEE_DESCRIPTION = (
    "按员工工号查询员工信息，返回姓名、部门、职级、入职日期、在职状态。"
    "工号是三位数字字符串，例如 001。"
)

ATTENDANCE_DESCRIPTION = (
    "查询某员工在某日期区间的打卡记录，含迟到/早退/缺卡/加班/请假/缺勤等状态。"
    "start_date 与 end_date 均为闭区间。「上周」这类相对表述必须先调 get_current_time "
    "确定具体日期，再填入本工具。"
)

ORDER_DESCRIPTION = (
    "查询销售订单，返回订单明细列表（单号、日期、客户、状态、金额、商品明细）。"
    "本工具不提供任何汇总数字；需要订单笔数或金额总额时，必须再调用 calculator "
    "根据返回的明细自行累加。"
)


def _payload(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False)


def _bad_range(start_date: date, end_date: date) -> str:
    return _payload(
        ok=False,
        error="end_date 不能早于 start_date，请检查日期区间",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )


def build_specs(store: MockDataStore) -> list[ToolSpec]:
    """基于给定数据仓构建三个内部数据工具。

    做成工厂而不是模块级常量：数据目录来自配置，测试还要注入临时目录，模块级常量
    会在导入时就把 store 钉死，无法替换。闭包捕获 store 也避免了模块级可变全局。
    """

    async def query_employee(emp_id: str) -> str:
        employee = store.get_employee(emp_id)
        if employee is None:
            return _payload(ok=False, error=f"没有找到工号 {emp_id} 对应的员工")
        return _payload(ok=True, employee=employee.model_dump(mode="json"))

    async def query_attendance(emp_id: str, start_date: date, end_date: date) -> str:
        if end_date < start_date:
            return _bad_range(start_date, end_date)
        records = store.list_attendance(emp_id, start_date, end_date)
        return _payload(
            ok=True,
            emp_id=emp_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            records=[record.model_dump(mode="json") for record in records],
        )

    async def query_orders(
        start_date: date,
        end_date: date,
        customer: str | None = None,
        status: OrderStatus | None = None,
    ) -> str:
        if end_date < start_date:
            return _bad_range(start_date, end_date)
        orders = store.list_orders(
            start_date=start_date,
            end_date=end_date,
            customer=customer,
            status=status,
        )
        if len(orders) > _MAX_ORDERS_PER_CALL:
            return _payload(
                ok=False,
                error=(
                    f"该区间共 {len(orders)} 条订单，超过单次 {_MAX_ORDERS_PER_CALL} 条上限，"
                    "请缩小日期区间后分批查询再累加"
                ),
            )
        return _payload(
            ok=True,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            orders=[order.model_dump(mode="json") for order in orders],
        )

    handlers: list[tuple[str, str, type[BaseModel], Callable[..., Awaitable[str]]]] = [
        ("query_employee", EMPLOYEE_DESCRIPTION, EmployeeArgs, query_employee),
        ("query_attendance", ATTENDANCE_DESCRIPTION, AttendanceArgs, query_attendance),
        ("query_orders", ORDER_DESCRIPTION, OrderArgs, query_orders),
    ]
    return [
        ToolSpec(name=name, description=description, parameters=parameters, handler=handler)
        for name, description, parameters, handler in handlers
    ]
