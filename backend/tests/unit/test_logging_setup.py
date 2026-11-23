"""logging_setup.py 单元测试（M1 第 2 项验收：落盘轮转、request_id、脱敏）。

用例中的「密钥」全部是构造出来的假值，只用于断言脱敏生效。
"""

import logging
from collections.abc import Iterator
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from app.core.ids import bind_request_id, unbind_request_id
from app.logging_setup import BACKUP_DAYS, redact, setup_logging

FAKE_API_KEY = "sk-live-abc123"
FAKE_BOT_SECRET = "WXBOT-fake-123"
FAKE_RESPONSE_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=FAKE-QUERY-KEY-9"


@pytest.fixture()
def restored_root_logger() -> Iterator[None]:
    """setup_logging 会替换 root handler；测试后还原，避免影响同进程其他用例。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def test_redact_masks_all_secret_shapes() -> None:
    text = (
        f"key {FAKE_API_KEY} | Authorization: Bearer eyJfakeheader9 | "
        f'bot_secret: "{FAKE_BOT_SECRET}" | response_url={FAKE_RESPONSE_URL}'
    )
    cleaned = redact(text)
    for fragment in (FAKE_API_KEY, "eyJfakeheader9", FAKE_BOT_SECRET, "FAKE-QUERY-KEY-9"):
        assert fragment not in cleaned
    assert "sk-***" in cleaned
    assert "Authorization: ***" in cleaned
    assert "response_url=***" in cleaned


def test_redact_keeps_normal_text() -> None:
    assert (
        redact("员工 001 上周出勤 5 天，订单合计 1280 元")
        == "员工 001 上周出勤 5 天，订单合计 1280 元"
    )


def test_logs_written_with_request_id_and_redaction(
    tmp_path: Path, restored_root_logger: None
) -> None:
    setup_logging(tmp_path, console=False)
    _rid, token = bind_request_id("req_unit_test")
    try:
        logger = logging.getLogger("app.unit_test")
        logger.info("LLM 调用开始 api_key=%s", FAKE_API_KEY)
        logger.error("企微回复失败 response_url=%s", FAKE_RESPONSE_URL)
    finally:
        unbind_request_id(token)

    app_log = (tmp_path / "app.log").read_text(encoding="utf-8")
    error_log = (tmp_path / "error.log").read_text(encoding="utf-8")

    # request_id 注入 + 脱敏 + 全量落盘
    assert "req_unit_test" in app_log
    assert FAKE_API_KEY not in app_log
    assert "api_key=***" in app_log
    assert "LLM 调用开始" in app_log
    # error.log 只收 ERROR 及以上，且同样脱敏
    assert "企微回复失败" in error_log
    assert "FAKE-QUERY-KEY-9" not in error_log
    assert "LLM 调用开始" not in error_log


def test_file_handlers_rotate_midnight_keep_14_days(
    tmp_path: Path, restored_root_logger: None
) -> None:
    setup_logging(tmp_path, console=False)
    file_handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)
    ]
    assert len(file_handlers) == 2
    for handler in file_handlers:
        assert handler.when.upper() == "MIDNIGHT"
        assert handler.backupCount == BACKUP_DAYS


def test_setup_logging_is_idempotent(tmp_path: Path, restored_root_logger: None) -> None:
    setup_logging(tmp_path, console=False)
    first_count = len(logging.getLogger().handlers)
    setup_logging(tmp_path, console=False)
    assert len(logging.getLogger().handlers) == first_count
