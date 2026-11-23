"""Mock 数据载入与查询（TDD §4.8：启动时一次性载入内存并建索引）。

- 员工按 id 建 dict 索引，单查 O(1)。
- 日期过滤统一用 datetime.date 比较，**闭区间** [start, end]。
- 载入即全量 model_validate：脏数据在启动期报错（FR-6-02 同一哲学），
  而不是查询时静默漏行。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.mock_api.schemas import (
    AttendanceRecord,
    Employee,
    Order,
    OrderStatus,
    OrderSummary,
)


def _load_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name} 顶层必须是 JSON 数组")
    return data


class MockDataStore:
    """data/mock/*.json 的内存只读视图。"""

    def __init__(self, data_dir: Path) -> None:
        employees = [Employee.model_validate(x) for x in _load_list(data_dir / "employees.json")]
        self._employees_by_id: dict[str, Employee] = {e.id: e for e in employees}
        self._employees: list[Employee] = employees
        self._attendance: list[AttendanceRecord] = [
            AttendanceRecord.model_validate(x) for x in _load_list(data_dir / "attendance.json")
        ]
        self._orders: list[Order] = [
            Order.model_validate(x) for x in _load_list(data_dir / "orders.json")
        ]

    # ── 员工 ────────────────────────────────────────────────────────
    def get_employee(self, emp_id: str) -> Employee | None:
        return self._employees_by_id.get(emp_id)

    def list_employees(
        self, *, dept: str | None = None, name: str | None = None, level: str | None = None
    ) -> list[Employee]:
        """姓名是**模糊匹配**（应对「张三」类提问），dept/level 精确匹配。"""
        result = self._employees
        if dept is not None:
            result = [e for e in result if e.dept == dept]
        if name is not None:
            result = [e for e in result if name in e.name]
        if level is not None:
            result = [e for e in result if e.level == level]
        return result

    # ── 考勤 ────────────────────────────────────────────────────────
    def list_attendance(
        self, emp_id: str, start_date: date, end_date: date
    ) -> list[AttendanceRecord]:
        return [
            r for r in self._attendance if r.emp_id == emp_id and start_date <= r.date <= end_date
        ]

    # ── 订单 ────────────────────────────────────────────────────────
    def list_orders(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        customer: str | None = None,
        status: OrderStatus | None = None,
    ) -> list[Order]:
        result = self._orders
        if start_date is not None:
            result = [o for o in result if o.date >= start_date]
        if end_date is not None:
            result = [o for o in result if o.date <= end_date]
        if customer is not None:
            result = [o for o in result if customer in o.customer]
        if status is not None:
            result = [o for o in result if o.status == status]
        return result

    @staticmethod
    def summarize(orders: list[Order]) -> OrderSummary:
        """按状态分桶汇总，销售额口径只计 paid（schemas.OrderSummary 有完整说明）。"""

        def _sum(status: OrderStatus) -> Decimal:
            return sum((o.amount for o in orders if o.status == status), Decimal("0"))

        return OrderSummary(
            count=len(orders),
            total_amount=_sum(OrderStatus.PAID),
            refunded_amount=_sum(OrderStatus.REFUNDED),
            pending_amount=_sum(OrderStatus.PENDING),
            cancelled_amount=_sum(OrderStatus.CANCELLED),
        )


@lru_cache
def get_store(data_dir: Path) -> MockDataStore:
    """按数据目录缓存的进程级单例（FastAPI 依赖注入入口）。

    参数是 Path 而非无参：测试可以用临时目录构造独立数据集，
    生产路径来自 settings.mock_data_dir，天然只有一份。
    """
    return MockDataStore(data_dir)
