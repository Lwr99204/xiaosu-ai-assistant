from pydantic import ValidationError

from dev_tools.wecom_spike import SpikeSettings, extract_text, redact_error


def make_settings() -> SpikeSettings:
    return SpikeSettings(
        _env_file=None,
        wecom_bot_id="bot-real-id",
        wecom_bot_secret="secret-real-value",
    )


def test_extract_text_from_valid_frame() -> None:
    frame = {"body": {"text": {"content": "你好"}}}
    assert extract_text(frame) == "你好"


def test_extract_text_rejects_unexpected_shape() -> None:
    assert extract_text({"body": {"text": []}}) == ""
    assert extract_text({"body": None}) == ""


def test_settings_reject_placeholder_credentials() -> None:
    try:
        SpikeSettings(
            _env_file=None,
            wecom_bot_id="your-wecom-bot-id",
            wecom_bot_secret="your-wecom-bot-secret",
        )
    except ValidationError as exc:
        assert exc.error_count() == 2
    else:  # pragma: no cover
        raise AssertionError("占位凭证必须被拒绝")


def test_error_redaction_hides_credentials() -> None:
    settings = make_settings()
    raw = "bot-real-id secret=secret-real-value token=abc123"
    redacted = redact_error(raw, settings)
    assert "bot-real-id" not in redacted
    assert "secret-real-value" not in redacted
    assert "abc123" not in redacted
