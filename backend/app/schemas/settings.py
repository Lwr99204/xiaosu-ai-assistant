"""管理后台模型设置契约；任何响应都不得包含 API Key。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str


class ModelSettings(BaseModel):
    current: str
    available: list[ModelInfo]


class ModelSwitchRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=100)


class ModelSwitchResponse(BaseModel):
    ok: bool
    current: str
