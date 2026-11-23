"""统一异常体系与错误码（TDD §4.9 / FR-6-07「永不裸 500」）。

设计原则
--------
1. **一个基类 `AppError` 承载全部对外信息**：错误码、HTTP 状态、是否可重试、
   面向用户的话术。上层（HTTP 处理器 / IM 兜底）只需读这几个字段，不必逐个
   isinstance 分支——新增异常类型无需改动调用方。
2. **区分「技术信息」与「用户话术」**：`message` 进日志（可含细节），
   `user_facing_msg` 才发给用户。混在一起是常见的信息泄露源（NFR-11）。
3. **`retryable` 是重试层的唯一判据**（见 `core/retry.py`）。4xx 类语义错误
   一律 `retryable=False`——重试一个无效 Key 只会白烧配额并拖长用户等待。
4. 本模块是**叶子层**：不得 import 项目内其他模块，否则与 `config.py`
   形成循环依赖（TDD §3.1）。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有业务异常的基类。子类覆盖类属性声明默认行为，调用方可就地覆盖。"""

    #: 稳定错误码，进响应体 `code` 字段，供前端/后台按码分流（不随文案变化）
    code: str = "APP_ERROR"
    #: 映射到 HTTP 的状态码
    http_status: int = 500
    #: 是否允许 `core/retry.py` 自动重试
    retryable: bool = False
    #: 面向开发者/日志的技术描述
    default_message: str = "服务内部错误"
    #: 面向最终用户的话术，必须「可行动」：告诉他下一步做什么，而非道歉了事
    default_user_message: str = "服务开小差了，请稍后重试；若持续出现请联系管理员"

    def __init__(
        self,
        message: str | None = None,
        *,
        user_facing_msg: str | None = None,
        code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        cause: BaseException | None = None,
        **context: Any,
    ) -> None:
        self.message = message or self.default_message
        self.user_facing_msg = user_facing_msg or self.default_user_message
        self.cause = cause
        #: 结构化上下文（如 emp_id / doc_id）进日志便于排查；**不得放密钥**
        self.context: dict[str, Any] = dict(context)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def to_response_dict(self, request_id: str | None = None) -> dict[str, Any]:
        """统一错误响应体（TDD §5.1）。

        🔴 始终带 `request_id`：用户报障时可直接拿它 grep 日志，这是 FR-6-06
        「全链路 request_id」唯一的价值兑现点，漏掉等于白做。

        `detail` 与 `message` 同值，用于兼容 TDD §4.8 对 mock 接口 404 响应体
        `{"detail": "员工 001 不存在"}` 的字面要求——两处文档口径在此合一，
        避免 mock 接口成为全项目唯一一种错误格式（已回写 TDD）。
        """
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "detail": self.message,
            "user_facing_msg": self.user_facing_msg,
            "request_id": request_id,
        }
        if self.context:
            body["context"] = self.context
        return body

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} code={self.code} "
            f"status={self.http_status} retryable={self.retryable}>"
        )


class ConfigError(AppError):
    """启动期配置缺失或非法（FR-6-02：启动即失败并说清缺哪个变量）。"""

    code = "CONFIG_ERROR"
    http_status = 500
    retryable = False
    default_message = "配置错误"
    default_user_message = "服务配置有误，请联系管理员检查 .env"


class NotFoundError(AppError):
    """资源不存在 → 404。

    🔴 TDD §4.9 的异常树里没有这一类，但 §4.8 明确要求 mock 接口对不存在的
    员工返回 404。缺了它就只能借用 FastAPI 的 HTTPException，从而绕过统一
    错误体（丢掉 request_id）。已回写 TDD §4.9。
    """

    code = "NOT_FOUND"
    http_status = 404
    retryable = False
    default_message = "资源不存在"
    default_user_message = "没有找到你要查的内容，请确认编号或换个说法"


# ── LLM（TDD §4.9：401/403 不重试，429/超时/5xx 重试 2 次后降级）───────
class LLMError(AppError):
    """模型服务异常基类。"""

    code = "LLM_ERROR"
    http_status = 502
    retryable = True
    default_message = "模型服务异常"
    default_user_message = "AI 服务暂时不可用，请稍后重试"


class LLMAuthError(LLMError):
    """401/403：Key 无效或权限不足。**不重试**——重试只会白烧配额。"""

    code = "LLM_AUTH_ERROR"
    retryable = False
    default_message = "模型服务鉴权失败"
    default_user_message = "AI 服务鉴权失败，请联系管理员检查 API Key"


class LLMRateLimitError(LLMError):
    """429：触发限流，退避后重试。"""

    code = "LLM_RATE_LIMIT"
    http_status = 429
    default_message = "模型服务限流"
    default_user_message = "当前提问的人有点多，请稍等几秒再试"


class LLMTimeoutError(LLMError):
    """请求超时（FR-4-09 硬超时兜底的触发点之一）。"""

    code = "LLM_TIMEOUT"
    http_status = 504
    default_message = "模型服务响应超时"
    default_user_message = "AI 响应超时了，请稍后重试或换个更简短的问法"


class LLMUnavailableError(LLMError):
    """5xx / 网络不可达。"""

    code = "LLM_UNAVAILABLE"
    default_message = "模型服务不可用"
    default_user_message = "AI 服务暂时连不上，请稍后重试"


# ── RAG ────────────────────────────────────────────────────────────────
class RAGError(AppError):
    """检索增强链路异常基类。"""

    code = "RAG_ERROR"
    default_message = "知识库处理异常"
    default_user_message = "知识库暂时无法处理该请求"


class ParseError(RAGError):
    """文档解析失败：把**该文档**置 failed，不影响其他文档（FR-1-08）。"""

    code = "DOC_PARSE_ERROR"
    http_status = 422
    default_message = "文档解析失败"
    default_user_message = "这个文件没能读出来，请确认格式（支持 md/txt/pdf/docx）后重传"


class EmbeddingError(RAGError):
    """向量化失败：本地 BGE-M3 服务未启动是首要嫌疑（TDD §1.4 / R17）。"""

    code = "EMBEDDING_ERROR"
    http_status = 502
    retryable = True
    default_message = "向量化失败"
    default_user_message = "向量服务不可用，请确认本地 embedding 服务（端口 8001）已启动"


# ── 工具与校验 ──────────────────────────────────────────────────────────
class ToolError(AppError):
    """工具执行失败。

    🔴 语义特殊：**不冒泡成 HTTP 500**，而是转成文本回灌给模型让它自纠
    （FR-3-08）。因此 `http_status` 很少被用到，保留只为体系完整。
    """

    code = "TOOL_ERROR"
    http_status = 400
    default_message = "工具执行失败"
    default_user_message = "查询内部数据时出错了，我换个方式再试一次"

    def __init__(
        self, message: str | None = None, *, tool_name: str | None = None, **kw: Any
    ) -> None:
        super().__init__(message, **kw)
        self.tool_name = tool_name
        if tool_name:
            self.context.setdefault("tool_name", tool_name)


class ValidationError(AppError):
    """入参校验失败 → 422 + **字段级**错误说明（FR-3-07）。

    字段级信息是给模型看的：回灌 `"start_date: 日期格式应为 YYYY-MM-DD"`，
    它才有机会自己改对；只回一句「参数错误」等于让它瞎猜。
    """

    code = "VALIDATION_ERROR"
    http_status = 422
    default_message = "入参校验失败"
    default_user_message = "参数格式不对，请补充或修正后重试"

    def __init__(
        self,
        message: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.errors: list[dict[str, Any]] = errors or []
        if self.errors:
            self.context.setdefault("errors", self.errors)
