"""健康状态按配置判断，不对 LLM 发起探测请求。"""

from pathlib import Path

from app.config import Settings
from app.main import build_health


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "llm_api_key": "test-secret-do-not-report",
        "log_dir": tmp_path / "logs",
        "storage_dir": tmp_path / "storage",
        "mock_data_dir": tmp_path / "mock",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_health_llm_mock_provider_is_ok(tmp_path: Path) -> None:
    health = build_health(_settings(tmp_path, llm_provider="mock"))

    assert health.llm.status == "ok"
    assert health.llm.detail == "mock provider"


def test_health_llm_openai_compat_shows_provider_and_model_only(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        llm_provider="openai_compat",
        llm_base_url="https://llm.example/v1",
        llm_model="qwen-test-model",
    )
    health = build_health(settings)

    assert health.llm.status == "ok"
    assert health.llm.detail == "provider=openai_compat, model=qwen-test-model"
    assert settings.llm_api_key.get_secret_value() not in (health.llm.detail or "")


def test_health_llm_incomplete_config_is_degraded(tmp_path: Path) -> None:
    health = build_health(_settings(tmp_path, llm_base_url="   "))

    assert health.llm.status == "degraded"
    assert health.llm.detail == "LLM 配置不完整"
