"""Mock 内部 API 的强类型 Schema（TDD §4.8，题目「数据结构强类型」底线）。

与 data/mock/*.json 一一对应；status 全部用 StrEnum 严格约束，脏数据在
载入阶段就报错，而不是查询时静默漏掉（repository.py 用 model_validate 全量校验）。
用 StrEnum 而不是 (str, Enum) 混入：str(member) 直接得到 "paid" 这类取值本身，
拼日志与错误文案时不会冒出 "OrderStatus.PAID" 这种内部表示。
金额用 Decimal：JSON 里是 "4200.00" 字符串，float 会产生 0.1+0.2 类误差，
汇总口径（summary.total_amount）必须精确。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel


class EmployeeStatus(StrEnum):
    """在职状态。left 员工仍可查询（历史考勤/订单关联），但不能被当作在职。"""

    ACTIVE = "active"
    LEFT = "left"


class Employee(BaseModel):
    id: str
    name: str
    dept: str
    level: str
    email: str
    hire_date: date
    status: EmployeeStatus
    phone: str
    manager_id: str | None = None
    leave_date: date | None = None


class AttendanceStatus(StrEnum):
    """考勤状态，覆盖 data/mock/attendance.json 的全部 9 种取值。"""

    NORMAL = "normal"
    LATE = "late"
    EARLY_LEAVE = "early_leave"
    MISSING_CHECK_IN = "missing_check_in"
    MISSING_CHECK_OUT = "missing_check_out"
    OVERTIME = "overtime"
    WEEKEND_OVERTIME = "weekend_overtime"
    LEAVE = "leave"
    ABSENT = "absent"


class AttendanceRecord(BaseModel):
    emp_id: str
    date: date
    check_in: str | None = None  # "HH:MM"；缺卡为 None
    check_out: str | None = None
    status: AttendanceStatus
    note: str | None = None


class OrderStatus(StrEnum):
    """订单状态。summary 的销售口径只计 PAID（refunded/cancelled 单列）。"""

    PAID = "paid"
    PENDING = "pending"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class OrderItem(BaseModel):
    name: str
    qty: int
    unit_price: Decimal


class Order(BaseModel):
    id: str
    amount: Decimal
    date: date
    customer: str
    status: OrderStatus
    items: list[OrderItem]


class EmployeeListResponse(BaseModel):
    employees: list[Employee]
    count: int


class AttendanceResponse(BaseModel):
    records: list[AttendanceRecord]
    count: int


class OrderSummary(BaseModel):
    """汇总口径（TDD §4.8「让销售额有明确口径可讨论」）：

    - count：本次查询命中的订单总数（含退款/取消）。
    - total_amount：**仅 paid** 订单金额合计，即通常说的「销售额」。
    - refunded_amount：refunded 订单金额合计，单列供对账。
    - pending_amount / cancelled_amount：其余状态单列，口径完整。
    """

    count: int
    total_amount: Decimal
    refunded_amount: Decimal
    pending_amount: Decimal
    cancelled_amount: Decimal


class OrdersResponse(BaseModel):
    """订单接口默认返回**明细** + summary（CURRENT_PLAN 修正 3）。

    验收题 7.2-5 必须出现 query_orders → calculator 的自主汇总链路，
    summary 只作为模型的交叉校验参照，不能替代明细。
    """

    orders: list[Order]
    summary: OrderSummary
