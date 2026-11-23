"""get_current_time 工具。

description 里那句「必须先调用本工具确定基准日期」是刻意写的（TDD §4.5.2）：验收题
7.2-5「上周一共多少订单」和 7.3-7「他上周来上班几天」都含相对时间，而模型既不知道
今天几号（训练语料里没有），也可能是一个跑了好几天的长连接进程，自己猜「上周」的
日期区间必然错。

除了当前时刻，本工具还直接算好本周 / 上周 / 本月的日期区间一起返回。「上周」到底是
「上周一到上周日的自然周」还是「往前推 7 天」是模型最容易搞错的一步，直接给它比让
它心算可靠。

注意 CURRENT_PLAN 修正 2：本项目**不再**把当前时间注入 system prompt（覆盖 TDD §7.2
的注入方案），时间基准一律走本工具，取舍理由见 app/agent/prompts.py 的模块 docstring。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.agent.tool_registry import ToolSpec

DEFAULT_TIMEZONE = "Asia/Shanghai"
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class TimeArgs(BaseModel):
    """get_current_time 入参。"""

    timezone: str = Field(
        default=DEFAULT_TIMEZONE,
        max_length=64,
        description="IANA 时区名，如 Asia/Shanghai、UTC；缺省按北京时间",
    )


def _resolve_zone(timezone: str) -> tuple[ZoneInfo, str]:
    """时区名非法时退回北京时间，而不是抛错：问时间的问题不该因为拼错时区就失败。"""
    try:
        return ZoneInfo(timezone), timezone
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE), DEFAULT_TIMEZONE


def _iso_week(moment: datetime, weeks_ago: int) -> dict[str, str]:
    """moment 所在自然周（周一为一周之首）往前推 weeks_ago 周的起止日期。"""
    monday = (moment - timedelta(weeks=weeks_ago, days=moment.weekday())).date()
    return {"start": monday.isoformat(), "end": (monday + timedelta(days=6)).isoformat()}


def current_time_payload(timezone: str = DEFAULT_TIMEZONE, *, now: datetime | None = None) -> dict:
    """结构化的当前时间与常用相对区间。now 可注入，便于测试写死预期。"""
    zone, resolved = _resolve_zone(timezone)
    moment = now.astimezone(zone) if now is not None else datetime.now(zone)
    today = moment.date()
    return {
        "ok": True,
        "timezone": resolved,
        "timezone_valid": resolved == timezone,
        "datetime": moment.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "time": moment.strftime("%H:%M:%S"),
        "weekday": _WEEKDAY_CN[moment.weekday()],
        "this_week": _iso_week(moment, 0),
        "last_week": _iso_week(moment, 1),
        "this_month": {"start": today.replace(day=1).isoformat(), "end": today.isoformat()},
    }


async def run(timezone: str = DEFAULT_TIMEZONE, *, now: datetime | None = None) -> str:
    """工具 handler。now 只供测试注入，注册中心调用时只会传 timezone。"""
    return json.dumps(current_time_payload(timezone, now=now), ensure_ascii=False)


DESCRIPTION = (
    "获取当前日期与时间，同时返回本周、上周、本月的起止日期区间。"
    "凡问题里出现「现在/今天/昨天/本周/上周/本月」等相对时间表述，"
    "必须先调用本工具确定基准日期，再推算具体日期区间，严禁自己猜日期。"
)

SPEC = ToolSpec(
    name="get_current_time",
    description=DESCRIPTION,
    parameters=TimeArgs,
    handler=run,
)
