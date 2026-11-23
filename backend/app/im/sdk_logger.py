"""企业微信 SDK 安全 logger（CURRENT_PLAN 修正 9 / M1 第 3 项）。

为什么必须替换 SDK 默认 logger
------------------------------
aibot SDK 的 DefaultLogger 直接 print 到 stderr，且 DEBUG 级会把**原始帧
整体 JSON** 打进日志（ws.py「Received push message: {...}」），员工消息正文、
临时 response_url 都会随之落盘。本模块提供 WSClientOptions.logger 的替身：

1. 转发到标准库 logging（logger 名「wecom.sdk」），从而经过 logging_setup 的
   轮转、request_id 注入与 RedactingFormatter 脱敏（NFR-11）。
2. **任何级别**都先把消息文本中的 JSON 负载剥成「类型 + 字节长度」摘要——
   即使有人把日志级别调到 DEBUG，原始消息帧也不会落盘（纵深防御）。
"""

from __future__ import annotations

import logging
import re

#: SDK 日志里出现的原始帧都是内联 JSON 对象（json.dumps(...) 的产物）。
_JSON_PAYLOAD = re.compile(r"\{.*\}", re.DOTALL)

#: SDK 内部日志统一挂在这个名字下，与项目自身日志（app.*）区分，
#: 便于单独调级：logging.getLogger("wecom.sdk").setLevel(logging.INFO)
SDK_LOGGER_NAME = "wecom.sdk"

_logger = logging.getLogger(SDK_LOGGER_NAME)


def strip_payloads(message: str) -> str:
    """把消息文本中的 JSON 负载替换为长度摘要，正文不落日志。"""

    def _summary(match: re.Match[str]) -> str:
        return "{...payload stripped, len=" + str(len(match.group(0))) + "}"

    return _JSON_PAYLOAD.sub(_summary, message)


class SafeSdkLogger:
    """实现 SDK 期望的鸭子类型接口：debug/info/warn/error(message, *args)。

    用法（M3 正式 adapter 与 Gate 0 spike 相同）：
    WSClientOptions(bot_id=..., secret=..., logger=SafeSdkLogger())
    """

    def debug(self, message: str, *args: object) -> None:
        _logger.debug("%s", self._sanitize(message, args))

    def info(self, message: str, *args: object) -> None:
        _logger.info("%s", self._sanitize(message, args))

    def warn(self, message: str, *args: object) -> None:
        _logger.warning("%s", self._sanitize(message, args))

    def warning(self, message: str, *args: object) -> None:
        """标准库习惯名的别名，兼容按 logging 接口调用的代码路径。"""
        self.warn(message, *args)

    def error(self, message: str, *args: object) -> None:
        _logger.error("%s", self._sanitize(message, args))

    @staticmethod
    def _sanitize(message: str, args: tuple[object, ...]) -> str:
        """拼接 SDK 传散的 args，再剥掉 JSON 负载。

        args 里同样可能带帧内容或异常对象（异常文本可能含凭证，交给
        RedactingFormatter 兜底），这里先 str() 后一并剥离。
        """
        rendered = message
        if args:
            rendered = message + " " + " ".join(str(a) for a in args)
        return strip_payloads(rendered)
