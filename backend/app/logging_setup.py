"""日志基础设施（TDD §4.2 / FR-6-05 落盘 / FR-6-06 request_id / NFR-11 脱敏）。

三条不可让步的约定
------------------
1. **落盘 + 轮转**：logs/app.log 收全量、logs/error.log 只收 ERROR 及以上，
   均按午夜轮转、保留 14 天；console 同步输出便于开发期观察（FR-6-05）。
2. **每条日志带 request_id**：值取自 core.ids 的 ContextVar，未绑定请求上下文时
   显示「-」（如启动期日志），绝不显示「None」（FR-6-06）。
3. **脱敏在 Formatter 层对「最终成品文本」执行**：消息模板、%s 参数插值结果、
   异常栈都会被覆盖，避免 logger.info("key=%s", api_key) 这类写法绕过逐字段
   检查。企业微信 SDK 会把 bot_id/secret 写进鉴权帧，异常栈与 DEBUG 日志是
   主要泄露面（CURRENT_PLAN 修正 9 / NFR-11）。
"""

from __future__ import annotations

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.ids import current_request_id

#: 统一日志格式（TDD §4.2）。request_id 由 RequestIdFilter 注入。
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(request_id)s | %(name)s | %(message)s"

#: 轮转保留天数（TDD §4.2：when="midnight", backupCount=14）。
BACKUP_DAYS = 14

#: 脱敏规则（顺序执行）。目标泄露面：LLM API Key、Bearer 头、企微凭证、
#: 临时 response_url 及其 query 中的 key/token。
_REDACT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # OpenAI 风格 API Key
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "sk-***"),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)(bearer\s+)[^\s\"',;]+"), r"\1***"),
    # URL query 里的 key= / token=（企微临时 response_url 的主要泄露点）
    (re.compile(r"(?i)\b(key|token)=[^\s\"',;&]+"), r"\1=***"),
    # 常见「密钥名 = 值」写法（含 JSON / 日志 kv / 异常文本）
    (
        re.compile(
            r"(?i)\b(secret|bot_id|bot_secret|api_key|apikey|password|authorization"
            r"|response_url|access_token)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"
        ),
        r"\1\2***",
    ),
)


def redact(text: str) -> str:
    """对最终日志文本做密钥与临时 URL 脱敏（NFR-11）。"""
    for pattern, replacement in _REDACT_RULES:
        text = pattern.sub(replacement, text)
    return text


class RequestIdFilter(logging.Filter):
    """把当前上下文的 request_id 注入每条 LogRecord（FR-6-06）。

    挂在 handler 上而不是 logger 上：子 logger 的记录传播到 root 时
    不会经过 root logger 的 filter，只有 handler 级 filter 能覆盖全部记录。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class RedactingFormatter(logging.Formatter):
    """先按常规格式化（含 args 插值与异常栈），再对成品整体脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(
    log_dir: Path,
    *,
    level: int = logging.INFO,
    console: bool = True,
) -> None:
    """初始化 root logger：console + app.log（全量）+ error.log（ERROR 及以上）。

    幂等：重复调用会先摘除并关闭旧 handler（Windows 下不关文件句柄会导致
    日志文件被锁、测试临时目录无法清理），再挂新的，日志不会重复输出。
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)

    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler())
    handlers.append(
        TimedRotatingFileHandler(
            log_dir / "app.log",
            when="midnight",
            backupCount=BACKUP_DAYS,
            encoding="utf-8",
        )
    )
    error_handler = TimedRotatingFileHandler(
        log_dir / "error.log",
        when="midnight",
        backupCount=BACKUP_DAYS,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    handlers.append(error_handler)

    formatter = RedactingFormatter(LOG_FORMAT)
    request_filter = RequestIdFilter()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(request_filter)
        root.addHandler(handler)
