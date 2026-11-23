"""Token 计数与成本折算（TDD §4.3「Token 与成本」/ FR-5-10 后台展示成本）。

优先级：**供应商回的真实 usage 永远第一**，本模块只在拿不到 usage 时估算。
Ollama、部分兼容网关的流式响应不回 usage，若直接当 0 处理，后台的 Token 与
成本列会全空，FR-5-10 就成了摆设。

关于估算方式
------------
TDD 原文写「用 tiktoken 估算」。实测后改成自实现的确定性估算，原因有二：
1. tiktoken 需要联网下载 BPE 词表，与「断网也能跑测试」的门禁冲突，
   且 uv.lock 里并无该依赖。
2. 上游是 qwen 系列，用的是自有词表，tiktoken 的 cl100k 对它本来也不准。
估算只求量级正确：中文按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。
结果一律标 estimated=True，后台据此区分「账单口径」与「估算口径」。
"""

from __future__ import annotations

from app.llm.base import ChatMessage, TokenUsage

#: 中文字符大致 1 字 1 token；非中文按 4 字符 1 token 估
ASCII_CHARS_PER_TOKEN = 4

#: 单价表（元 / 千 token），(输入, 输出)。
#: ⚠️ 开发期占位值，正式上线前需按供应商官网价目核对；表里没有的模型按
#:    FALLBACK_PRICING 计，成本列显示估算值而不是空白。
PRICING: dict[str, tuple[float, float]] = {
    "qwen3.8-max": (0.0024, 0.0096),
    "qwen-plus": (0.0008, 0.002),
    "glm-4.6": (0.004, 0.012),
    "deepseek-chat": (0.001, 0.002),
}

FALLBACK_PRICING: tuple[float, float] = (0.002, 0.008)


def estimate_tokens(text: str | None) -> int:
    """估算一段文本的 token 数。空串返回 0，不抛异常。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    other = len(text) - cjk
    return cjk + -(-other // ASCII_CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[ChatMessage] | tuple[ChatMessage, ...]) -> int:
    """估算一组消息的 token 数。

    每条消息另加 4：OpenAI 协议里 role、分隔符与结尾标记本身也占 token，
    不加这部分估算值会系统性偏低。
    """
    total = 0
    for message in messages:
        total += estimate_tokens(message.content) + 4
        for call in message.tool_calls:
            total += estimate_tokens(call.name) + estimate_tokens(call.arguments)
    return total


def price_of(model: str) -> tuple[float, float]:
    """取模型单价（元/千 token）。未知模型回退到 FALLBACK_PRICING。"""
    return PRICING.get(model, FALLBACK_PRICING)


def cost_cny(usage: TokenUsage) -> float:
    """按 usage 折算人民币成本，保留 6 位小数（单次问答成本量级在分以下）。"""
    input_price, output_price = price_of(usage.model)
    cost = usage.prompt_tokens / 1000 * input_price
    cost += usage.completion_tokens / 1000 * output_price
    return round(cost, 6)


def usage_or_estimate(
    raw_usage: object | None,
    *,
    model: str,
    messages: list[ChatMessage] | tuple[ChatMessage, ...] = (),
    completion_text: str | None = None,
) -> TokenUsage:
    """把供应商返回的 usage 转成 TokenUsage；缺失或全 0 时退回估算。

    🔴「全 0 也算缺失」是实测得来的：流式请求若忘了带
    stream_options={"include_usage": true}，上游会回一个所有字段为 0 的 usage
    帧（TDD §1.3）。把这种 0 当真值记进库，后台成本会永远显示 0.00，
    比显示估算值更容易误导人。
    """
    data = getattr(raw_usage, "model_dump", None)
    values = data() if callable(data) else (raw_usage if isinstance(raw_usage, dict) else {})
    prompt = int(values.get("prompt_tokens") or 0)
    completion = int(values.get("completion_tokens") or 0)
    total = int(values.get("total_tokens") or 0)

    if prompt or completion or total:
        return TokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total or prompt + completion,
            model=model,
        )

    est_prompt = estimate_messages_tokens(messages)
    est_completion = estimate_tokens(completion_text)
    return TokenUsage(
        prompt_tokens=est_prompt,
        completion_tokens=est_completion,
        total_tokens=est_prompt + est_completion,
        model=model,
        estimated=True,
    )
