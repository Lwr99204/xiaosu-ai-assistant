"""拒答与引用后校验，以及全部固定话术常量。

三层防线（TDD §4.4.4）：
L1 敏感主题前置拦截：家庭住址 / 身份证 / 银行卡 / 薪资 / 病历等隐私问题不进模型，
   直接拒答。规则拦截是唯一能保证 100% 不泄露的手段，prompt 里的约束只是第二道。
L2 检索阈值拒答：由检索器在 M4 落地（低于 retrieval_min_score 视为没查到），
   表现为知识库工具返回空命中，模型据 system prompt 第 3 条给出不知道话术。
L3 引用后校验：答案里的 [n] 必须落在实际命中范围内，否则视为编造引用，撤回答案。

🔴 L2/L3 的适用范围（CURRENT_PLAN 修正 1）：**只对知识库来源的答案生效**。
员工 / 考勤 / 订单 / 时间 / 计算器这些工具的答案根本没有「文档引用」概念，
若一律要求带 [n] 才能回答，验收题 Q4-Q7 会被自己的校验逻辑拒掉。

话术常量集中在此而不是 prompts.py：prompts.py 的 SYSTEM_PROMPT 必须与 TDD §7.1
逐字一致（有脚本校验），拒答与降级话术是代码路径的一部分，改动频率与评审口径都不同。
其中 PRIVACY_REFUSAL 与 NOT_FOUND_REPLY 两句刻意与 system prompt 第 7 条、第 3 条
的措辞完全相同，这样模型自己拒答与被代码拦截时用户看到的文本一致。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# system prompt 第 7 条列举的隐私类别，末尾「等个人隐私」是非穷举表述，
# 因此把联系方式一并纳入：员工手机号在 mock 数据里真实存在，泄露代价同样高。
SENSITIVE_PATTERNS: tuple[str, ...] = (
    "家庭住址",
    "家住",
    "住址",
    "居住地址",
    "户籍地址",
    "身份证",
    "银行卡",
    "工资卡",
    "薪资",
    "薪水",
    "工资",
    "年薪",
    "病历",
    "病史",
    "手机号",
    "电话号码",
)

PRIVACY_REFUSAL = "这类信息涉及个人隐私，我无法提供"

NOT_FOUND_REPLY = "文档里没有找到相关信息，建议换个说法再问，或联系 HR。"

#: 检索阈值拒答话术（TDD §4.4.4 表格第一行）。带上查询词是刻意的：让用户确认系统
#: 听懂了问题、只是文档里确实没有，而不是答非所问。与 NOT_FOUND_REPLY 的区别在于
#: 前者由代码在「命中为空」时直接返回（不进模型），后者是模型自己按 system prompt
#: 第 3 条给出的回答，或 L3 撤回后使用的兜底文本。
THRESHOLD_REFUSAL_TEMPLATE = (
    "我在公司文档里没有找到和「{query}」相关的内容，建议换个说法再问，或联系 HR。"
)

TOOL_ROUNDS_EXCEEDED_REPLY = "这个问题有点复杂，我没能得出确定答案，建议拆分后重新提问。"

# 验收题 Q10：把 API Key 改成无效值后，IM 里必须收到友好回复，
# 不能让用户那边一直转圈或永久无响应。
LLM_FAILURE_REPLY = (
    "抱歉，我暂时连不上模型服务，这次没能回答你的问题。请稍后再试一次；如果一直失败，请联系管理员。"
)

_CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")


def detect_sensitive(question: str) -> str | None:
    """命中敏感主题返回命中词，否则返回 None。

    只返回命中词本身，不返回用户原话上下文：这个值会被写进日志的 refused_by 字段，
    带上原话等于把用户提问原文落盘（FR-6-08 日志脱敏）。
    """
    for pattern in SENSITIVE_PATTERNS:
        if pattern in question:
            return pattern
    return None


def threshold_refusal(query: str) -> str:
    """生成检索阈值拒答话术。query 为空时退化成通用话术，避免出现空的「「」」。"""
    return THRESHOLD_REFUSAL_TEMPLATE.format(query=query) if query else NOT_FOUND_REPLY


def extract_citations(answer: str) -> list[int]:
    """按出现顺序提取答案里的 [n] 编号并去重。

    上限三位数字：[2026] 这种年份写法不该被当成引用编号。
    """
    seen: list[int] = []
    for match in _CITATION_PATTERN.finditer(answer):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen


def check_citations(answer: str, hits: Sequence[Mapping[str, Any]]) -> str | None:
    """知识库答案的引用后校验（L3）。返回拒答原因，通过则返回 None。

    两类失败：
    - 用了知识库却一个 [n] 都没标 → 无法核实来源，按编造处理
    - [n] 超出实际命中数量 → 模型在引用不存在的资料
    """
    cited = extract_citations(answer)
    if not cited:
        return "答案使用了知识库内容但未标注任何 [n] 引用"
    out_of_range = [number for number in cited if number < 1 or number > len(hits)]
    if out_of_range:
        return f"引用编号超出实际命中范围：{out_of_range}，本次仅命中 {len(hits)} 条"
    return None


def pick_citations(answer: str, hits: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """按答案实际引用的编号，从命中里取出对应条目，供后台展示原文定位。"""
    return tuple(
        dict(hits[number - 1]) for number in extract_citations(answer) if 1 <= number <= len(hits)
    )
