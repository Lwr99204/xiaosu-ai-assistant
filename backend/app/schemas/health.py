"""健康检查响应契约（TDD §5.1 GET /api/health）。

为什么每个组件单独建模，而不是只回一个 status=ok：
考题功能 6 要求「错误兜底」可观测，M5 后台要显示 IM 接入状态，评审也要能一眼
看出「服务起来了但向量库还没接」。把每个组件的接线状态显式化，
比等到调用失败才发现要便宜得多。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

#: 组件状态。not_wired 表示「本里程碑还没接」，是预期状态而非故障
ComponentState = Literal["ok", "not_wired", "degraded", "down"]


class HealthComponent(BaseModel):
    """单个依赖组件的状态。detail 用于给出可行动的下一步（如「端口 8001 未启动」）。"""

    status: ComponentState
    detail: str | None = None


class WecomHealth(BaseModel):
    """企业微信长连接状态。enabled=False 时 connected 必然为 False，属正常配置态。"""

    enabled: bool
    connected: bool
    last_heartbeat: datetime | None = None


class ImHealth(BaseModel):
    wecom: WecomHealth


class HealthResponse(BaseModel):
    """/api/health 响应体。

    顶层 status 只反映「HTTP 服务自身能否正常应答」：组件 not_wired 不影响它，
    否则 M1 阶段会一直显示 degraded，把真正的故障盖住。
    """

    status: Literal["ok", "degraded", "down"]
    version: str
    llm: HealthComponent
    embed: HealthComponent
    vector_store: HealthComponent
    db: HealthComponent
    im: ImHealth
