"""Mock 内部 API 路由（TDD §4.8 / 考题「功能 3 工具 A：内部 API 必做」）。

这一层只做协议转换：HTTP query 参数 -> repository 查询 -> Pydantic 响应模型。
业务口径（姓名模糊匹配、日期闭区间、销售额只计 paid）全在 repository 与 schemas
里定义，路由不重复实现，避免两处口径漂移。

🔴 404 响应体必须同时满足两份文档：考题 §7.2-4 要求模型能拿到「员工 001 不存在」
这类可读结论，TDD §4.9 要求全项目统一错误体（带 code 与 request_id）。
core.errors.AppError.to_response_dict 已把 detail 与 message 同值输出，两处合一。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors import NotFoundError, ValidationError
from app.mock_api.repository import MockDataStore
from app.mock_api.schemas import (
    AttendanceResponse,
    Employee,
    EmployeeListResponse,
    OrdersResponse,
    OrderStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mock-api"])


def get_store(request: Request) -> MockDataStore:
    """从 app.state 取内存数据视图（create_app 启动时载入一次）。

    走 request.app.state 而不是直接 import repository 的全局单例：测试可以用
    app.dependency_overrides 换掉数据源，生产路径仍由 settings.mock_data_dir 决定。
    """
    return request.app.state.store


StoreDep = Annotated[MockDataStore, Depends(get_store)]


def _require_date_range(start: date | None, end: date | None) -> None:
    """日期区间合法性：start 晚于 end 是调用方（多半是模型）填错，
    必须给出字段级说明让它有机会自纠（FR-3-07），而不是返回空数组让它误判成「没有数据」。
    """
    if start is not None and end is not None and start > end:
        raise ValidationError(
            "日期区间非法：start_date 晚于 end_date",
            errors=[
                {
                    "field": "start_date",
                    "message": f"start_date({start}) 不能晚于 end_date({end})",
                    "type": "date_range",
                }
            ],
        )


@router.get(
    "/employee/{emp_id}",
    response_model=Employee,
    summary="按工号查单个员工",
)
def read_employee(emp_id: str, store: StoreDep) -> Employee:
    employee = store.get_employee(emp_id)
    if employee is None:
        # 文案与 TDD §4.8 字面一致，模型可以直接把它转述给用户
        raise NotFoundError(f"员工 {emp_id} 不存在", emp_id=emp_id)
    return employee


@router.get(
    "/employee",
    response_model=EmployeeListResponse,
    summary="员工列表（姓名模糊 / 部门·职级精确）",
)
def list_employees(
    store: StoreDep,
    dept: Annotated[str | None, Query(description="部门名，精确匹配，如 技术部")] = None,
    name: Annotated[str | None, Query(description="姓名片段，模糊匹配，如 张")] = None,
    level: Annotated[str | None, Query(description="职级，精确匹配，如 P6")] = None,
) -> EmployeeListResponse:
    employees = store.list_employees(dept=dept, name=name, level=level)
    return EmployeeListResponse(employees=employees, count=len(employees))


@router.get(
    "/attendance",
    response_model=AttendanceResponse,
    summary="考勤记录（日期闭区间）",
)
def list_attendance(
    store: StoreDep,
    emp_id: Annotated[str, Query(description="员工工号，如 001")],
    start_date: Annotated[date, Query(description="起始日期 YYYY-MM-DD，含当天")],
    end_date: Annotated[date, Query(description="结束日期 YYYY-MM-DD，含当天")],
) -> AttendanceResponse:
    _require_date_range(start_date, end_date)
    if store.get_employee(emp_id) is None:
        raise NotFoundError(f"员工 {emp_id} 不存在", emp_id=emp_id)
    records = store.list_attendance(emp_id, start_date, end_date)
    return AttendanceResponse(records=records, count=len(records))


@router.get(
    "/orders",
    response_model=OrdersResponse,
    summary="订单明细 + 汇总（默认返回明细，供模型自行计算）",
)
def list_orders(
    store: StoreDep,
    start_date: Annotated[date | None, Query(description="起始日期 YYYY-MM-DD，含当天")] = None,
    end_date: Annotated[date | None, Query(description="结束日期 YYYY-MM-DD，含当天")] = None,
    customer: Annotated[str | None, Query(description="客户名片段，模糊匹配")] = None,
    status: Annotated[OrderStatus | None, Query(description="订单状态过滤")] = None,
) -> OrdersResponse:
    """🔴 默认返回**明细**而不是只给汇总（CURRENT_PLAN 修正 3）。

    考题 §7.2-5「上周一共多少订单」的验收点在于模型自己走
    query_orders -> calculator 完成汇总；如果接口直接吐一个 count，
    模型就没有机会展示工具编排能力，这一题等于白送分。
    summary 保留是给模型做交叉校验的参照，口径见 schemas.OrderSummary。
    """
    _require_date_range(start_date, end_date)
    orders = store.list_orders(
        start_date=start_date, end_date=end_date, customer=customer, status=status
    )
    return OrdersResponse(orders=orders, summary=MockDataStore.summarize(orders))
