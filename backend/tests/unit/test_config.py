"""config.py 单元测试（M1 第 1 项验收：非法配置启动即失败、路径锚定仓库根）。

全部用例都用 _env_file=None 隔离本机 .env，测试不依赖也不读取真实凭证。
"""

import pytest
from pydantic import ValidationError

from app.config import REPO_ROOT, Settings

FAKE_KEY = "sk-test-only-not-real"


def make_settings(**overrides: object) -> Settings:
    base: dict = {"_env_file": None, "llm_api_key": FAKE_KEY}
    return Settings(**{**base, **overrides})


def test_missing_api_key_fails_fast() -> None:
    """缺 LLM_API_KEY 必须在构造期抛错（FR-6-02），不允许带病启动。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_typed_value_fails_fast() -> None:
    """类型非法（温度不是数字）同样启动即失败。"""
    with pytest.raises(ValidationError):
        make_settings(llm_temperature="not-a-number")


def test_secret_fields_masked_in_repr() -> None:
    """SecretStr 保证 repr / str / 日志整体打印不泄露原文（NFR-10/11）。"""
    settings = make_settings(wecom_bot_secret="super-secret-test")
    dumped = repr(settings) + str(settings)
    assert FAKE_KEY not in dumped
    assert "super-secret-test" not in dumped
    assert settings.llm_api_key.get_secret_value() == FAKE_KEY


def test_relative_paths_resolve_to_repo_root() -> None:
    """相对路径一律锚定仓库根（CURRENT_PLAN M1 第 1 项）。"""
    settings = make_settings()
    assert settings.log_dir == REPO_ROOT / "logs"
    assert settings.mock_data_dir == REPO_ROOT / "data" / "mock"
    assert settings.sqlite_path.is_absolute()
    assert settings.storage_dir.is_absolute()


def test_whitelist_accepts_json_and_csv() -> None:
    """.env 里 JSON 数组与逗号分隔两种写法都能解析。"""
    json_style = make_settings(wecom_allowed_user_ids='["zhangsan", "lisi"]')
    csv_style = make_settings(wecom_allowed_user_ids="zhangsan, lisi")
    empty = make_settings(wecom_allowed_user_ids="[]")
    assert json_style.wecom_allowed_user_ids == ["zhangsan", "lisi"]
    assert csv_style.wecom_allowed_user_ids == ["zhangsan", "lisi"]
    assert empty.wecom_allowed_user_ids == []
