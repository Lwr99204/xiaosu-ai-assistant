"""管理后台模型设置 API；只暴露模型标识，不返回任何凭证。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request

from app.core.errors import ValidationError
from app.schemas.settings import (
    ModelInfo,
    ModelSettings,
    ModelSwitchRequest,
    ModelSwitchResponse,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_KNOWN_MODELS = (
    ("deepseek-v4-pro", "DeepSeek V4 Pro"),
    ("qwen3.8-max", "Qwen 3.8 Max"),
    ("glm-5.3", "GLM 5.3"),
)


def _available(provider: str) -> list[ModelInfo]:
    return [ModelInfo(id=model_id, name=name, provider=provider) for model_id, name in _KNOWN_MODELS]


@router.get("/model", response_model=ModelSettings)
def get_model_settings(request: Request) -> ModelSettings:
    settings = request.app.state.settings
    available = _available(settings.llm_provider)
    if settings.llm_model not in {item.id for item in available}:
        available.insert(
            0,
            ModelInfo(
                id=settings.llm_model,
                name=settings.llm_model,
                provider=settings.llm_provider,
            ),
        )
    return ModelSettings(current=settings.llm_model, available=available)


@router.put("/model", response_model=ModelSwitchResponse)
def switch_model(request: Request, payload: ModelSwitchRequest) -> ModelSwitchResponse:
    allowed = {item.id for item in _available(request.app.state.settings.llm_provider)}
    if payload.model_id not in allowed:
        raise ValidationError(
            "不支持的模型",
            errors=[{"field": "model_id", "message": "模型不在可选列表中"}],
        )
    switcher: Callable[[str], None] = request.app.state.switch_model
    switcher(payload.model_id)
    return ModelSwitchResponse(ok=True, current=payload.model_id)
