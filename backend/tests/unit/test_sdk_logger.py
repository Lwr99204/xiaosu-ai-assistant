"""SafeSdkLogger 单元测试（CURRENT_PLAN 修正 9 / M1 第 3 项）。

这些断言对应一个具体泄露面：aibot SDK 的 ws.py 在 DEBUG 级会打印
"Received push message: <整帧 JSON>"，帧里同时含员工消息正文与临时 response_url。
本测试保证无论日志级别调到哪一级，JSON 负载都只以长度摘要形式落盘。
"""

from __future__ import annotations

import json
import logging

import pytest

from app.im.sdk_logger import SDK_LOGGER_NAME, SafeSdkLogger, strip_payloads

#: 形状与 SDK 真实推送帧一致，内容全部为虚构值
FAKE_FRAME = {
    "msgtype": "text",
    "chatid": "wr_fake_chat",
    "text": {"content": "我的年假还剩几天"},
    "response_url": "https://qyapi.example.com/callback/tmp-token-abc123",
}

RAW_FRAME = json.dumps(FAKE_FRAME, ensure_ascii=False)


def _one_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    sdk_records = [r for r in caplog.records if r.name == SDK_LOGGER_NAME]
    assert len(sdk_records) == 1
    return sdk_records[0]


def test_strip_payloads_replaces_json_with_length_summary() -> None:
    result = strip_payloads("Received push message: " + RAW_FRAME)

    assert "年假" not in result
    assert "tmp-token-abc123" not in result
    assert "{...payload stripped, len=" + str(len(RAW_FRAME)) + "}" in result


def test_strip_payloads_keeps_plain_text_intact() -> None:
    assert strip_payloads("connected to wss endpoint") == "connected to wss endpoint"


def test_strip_payloads_handles_multiline_frame() -> None:
    raw = "frame:\n" + json.dumps(FAKE_FRAME, indent=2, ensure_ascii=False) + "\ndone"
    result = strip_payloads(raw)

    assert "年假" not in result
    assert result.endswith("done")


def test_info_is_routed_to_wecom_sdk_logger(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=SDK_LOGGER_NAME):
        SafeSdkLogger().info("websocket connected")

    record = _one_record(caplog)
    assert record.levelno == logging.INFO
    assert record.getMessage() == "websocket connected"


def test_debug_strips_payload_even_at_debug_level(caplog: pytest.LogCaptureFixture) -> None:
    """纵深防御：把级别调到 DEBUG 也不能让原始帧落盘。"""
    with caplog.at_level(logging.DEBUG, logger=SDK_LOGGER_NAME):
        SafeSdkLogger().debug("Received push message: %s", RAW_FRAME)

    message = _one_record(caplog).getMessage()
    assert "年假" not in message
    assert "response_url" not in message
    assert "{...payload stripped, len=" in message


def test_extra_args_are_joined_then_stripped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=SDK_LOGGER_NAME):
        SafeSdkLogger().info("send failed", RAW_FRAME, 500)

    message = _one_record(caplog).getMessage()
    assert message.startswith("send failed")
    assert "500" in message
    assert "年假" not in message


def test_warn_maps_to_warning_level(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=SDK_LOGGER_NAME):
        SafeSdkLogger().warn("heartbeat delayed")

    assert _one_record(caplog).levelno == logging.WARNING


def test_warning_alias_behaves_like_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=SDK_LOGGER_NAME):
        SafeSdkLogger().warning("heartbeat delayed")

    assert _one_record(caplog).levelno == logging.WARNING


def test_error_level_is_preserved(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=SDK_LOGGER_NAME):
        SafeSdkLogger().error("auth rejected: " + RAW_FRAME)

    record = _one_record(caplog)
    assert record.levelno == logging.ERROR
    assert "auth rejected" in record.getMessage()
    assert "年假" not in record.getMessage()
