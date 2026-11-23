"""企业微信 Gate 0 连通性探针。

只验证一件事：收到一条真实文本消息后，回复一条固定文本。
不接入 Agent，不打印消息正文、原始帧或凭证。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_REPLY = "✅ 小苏 Gate 0 连通成功：企业微信消息收发正常。"
PLACEHOLDER_PREFIXES = ("your-", "replace-", "example-")


class SpikeSettings(BaseSettings):
    """探针所需的最小配置。"""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    wecom_bot_id: str
    wecom_bot_secret: SecretStr
    wecom_ws_url: str = "wss://openws.work.weixin.qq.com"

    @field_validator("wecom_bot_id")
    @classmethod
    def reject_placeholder_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned.lower().startswith(PLACEHOLDER_PREFIXES):
            raise ValueError("WECOM_BOT_ID 仍是占位值")
        return cleaned

    @field_validator("wecom_bot_secret")
    @classmethod
    def reject_placeholder_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw or raw.lower().startswith(PLACEHOLDER_PREFIXES):
            raise ValueError("WECOM_BOT_SECRET 仍是占位值")
        return SecretStr(raw)


def extract_text(frame: dict[str, Any]) -> str:
    """从 SDK 文本帧中安全提取文本，不假设其他字段存在。"""

    body = frame.get("body")
    if not isinstance(body, dict):
        return ""
    text = body.get("text")
    if not isinstance(text, dict):
        return ""
    content = text.get("content")
    return content if isinstance(content, str) else ""


def redact_error(error: object, settings: SpikeSettings) -> str:
    """避免 SDK 异常把 bot_id/secret 带到控制台。"""

    message = str(error)
    secrets = (settings.wecom_bot_id, settings.wecom_bot_secret.get_secret_value())
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return re.sub(r"(?i)(secret|token|authorization)[=: ]+[^\s,;]+", r"\1=***", message)


async def run_spike(settings: SpikeSettings, timeout_s: float, once: bool) -> None:
    """连接企业微信，认证成功后等待文本并固定回复。"""

    try:
        from aibot import WSClient, WSClientOptions, generate_req_id
    except ImportError as exc:  # pragma: no cover - 只在依赖未安装时触发
        raise RuntimeError("缺少 SDK，请先在 backend 目录执行 uv sync --frozen") from exc

    client = WSClient(
        WSClientOptions(
            bot_id=settings.wecom_bot_id,
            secret=settings.wecom_bot_secret.get_secret_value(),
            ws_url=settings.wecom_ws_url,
        )
    )
    authenticated = asyncio.Event()
    replied = asyncio.Event()
    connection_error: list[str] = []

    @client.on("connected")
    def on_connected() -> None:
        print("[Gate 0] WebSocket 已连接，等待鉴权……")

    @client.on("authenticated")
    def on_authenticated() -> None:
        print("[Gate 0] 鉴权成功。请在企业微信中给机器人发送任意文本。")
        authenticated.set()

    @client.on("error")
    def on_error(error: object) -> None:
        safe_message = redact_error(error, settings)
        connection_error.append(safe_message)
        print(f"[Gate 0] 连接错误：{safe_message}", file=sys.stderr)

    @client.on("message.text")
    async def on_text(frame: dict[str, Any]) -> None:
        content = extract_text(frame)
        print(f"[Gate 0] 收到文本消息（长度 {len(content)}，正文不落日志）")
        stream_id = generate_req_id("gate0")
        await client.reply_stream(frame, stream_id, FIXED_REPLY, True)
        print("[Gate 0] 固定回复已发送，消息收发闭环通过。")
        replied.set()

    try:
        await client.connect()
        await asyncio.wait_for(authenticated.wait(), timeout=timeout_s)
        if once:
            await asyncio.wait_for(replied.wait(), timeout=timeout_s)
        else:
            await asyncio.Event().wait()
    except TimeoutError as exc:
        detail = connection_error[-1] if connection_error else "未收到平台事件"
        raise RuntimeError(f"Gate 0 超时（{detail}）") from exc
    finally:
        client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="企业微信智能机器人 Gate 0 连通性探针")
    parser.add_argument("--check", action="store_true", help="只校验本地配置，不联网")
    parser.add_argument("--keep-running", action="store_true", help="回复后继续监听")
    parser.add_argument("--timeout", type=float, default=90.0, help="鉴权/消息等待超时秒数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        settings = SpikeSettings()
    except Exception as exc:
        print(
            "[Gate 0] 配置未就绪：请复制 .env.example 为 .env，并填写企业微信凭证。",
            file=sys.stderr,
        )
        print(f"[Gate 0] {exc}", file=sys.stderr)
        return 2

    print("[Gate 0] 配置校验通过（凭证内容不会输出）。")
    if args.check:
        return 0

    try:
        asyncio.run(run_spike(settings, timeout_s=args.timeout, once=not args.keep_running))
    except KeyboardInterrupt:
        print("\n[Gate 0] 已由用户停止。")
        return 130
    except Exception as exc:
        print(f"[Gate 0] 未通过：{redact_error(exc, settings)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
