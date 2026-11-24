"""全局配置（TDD §4.1 / FR-6-02「启动即校验」/ FR-6-03「配置全部外置」）。

设计要点
--------
1. 全部环境变量经 pydantic-settings 强类型化，构造即校验：缺 `LLM_API_KEY`
   这类必填项时直接抛 ValidationError，让进程启动失败并给出明确报错，
   而不是运行到一半 500（FR-6-02）。
2. 所有密钥字段用 `SecretStr`：repr / 日志 / 异常栈中自动显示 `**********`，
   从源头堵住泄露面（NFR-10/11）。
3. 相对路径一律以**仓库根目录**为基准解析成绝对路径——无论从仓库根、
   `backend/` 还是 `scripts/` 启动进程，`storage/`、`logs/`、`data/mock`
   都指向同一处，不会出现「换个目录启动就丢数据」。
4. 本模块是配置叶子层：不 import 项目内其他模块（TDD §3.1），
   避免与 `core/` 形成循环依赖。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根目录。config.py 位于 backend/app/ 下，向上两级即根。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 需要锚定到仓库根的相对路径字段（TDD §4.1「存储」小节）。
_PATH_FIELDS = ("storage_dir", "sqlite_path", "chroma_dir", "log_dir", "mock_data_dir")

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """全量环境变量的强类型视图，与 `.env.example` 一一对应（FR-6-03）。"""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM（OpenAI 兼容协议，TDD §4.1 / §1.3 实测）─────────────────
    llm_provider: Literal["openai_compat", "mock"] = "openai_compat"
    llm_api_key: SecretStr  # 必填：缺 key 直接启动失败
    llm_base_url: str = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.8-max"
    llm_temperature: float = 0.1  # 问答场景压低随机性
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2  # 与 core/retry.py 的默认值一致

    # ── Embedding（本地 BGE-M3 独立服务；与 chat 端点分离，CURRENT_PLAN 修正 5）──
    embed_base_url: str = "http://127.0.0.1:8001/v1"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024  # 🔴 实测值；改动必须重建索引（TDD R8）
    embed_batch_size: int = 16
    embed_api_key: str = "not-needed"  # 本地服务不校验，但 OpenAI 客户端要求非空
    embed_timeout_s: float = 60.0  # 首次请求含模型预热，留足余量

    # ── RAG ────────────────────────────────────────────────────────
    chunk_max_chars: int = 600
    chunk_overlap_chars: int = 80
    retrieval_top_k: int = 8
    retrieval_min_score: float = 0.63  # 标定中点 0.6314：正样本最低 0.6888，噪声最高 0.5741
    retrieval_min_spread: float = 0.01  # Q3 多个相关块接近时 spread=0.0105

    # ── Agent ──────────────────────────────────────────────────────
    agent_max_tool_rounds: int = 5  # FR-3-06
    agent_history_max_turns: int = 10  # FR-2-06

    # ── 存储（相对路径在 resolve_paths 中统一锚定到仓库根）───────────
    storage_dir: Path = Path("storage")
    sqlite_path: Path = Path("storage/xiaosu.db")
    chroma_dir: Path = Path("storage/chroma")
    log_dir: Path = Path("logs")
    mock_data_dir: Path = Path("data/mock")

    # ── IM（企业微信智能机器人长连接，TDD §1.5 / ADR-17）──────────────
    wecom_enabled: bool = False  # 无凭证时默认关闭，不阻塞其他功能
    wecom_bot_id: SecretStr | None = None
    wecom_bot_secret: SecretStr | None = None  # 🔴 SDK 会写进鉴权帧，必须 SecretStr
    wecom_ws_url: str = "wss://openws.work.weixin.qq.com"  # 出向网关，免公网入口
    wecom_allowed_user_ids: list[str] = []  # 空 = 不限制（生产强烈建议非空）
    wecom_max_reply_chars: int = 4000
    im_stream_chunk_chars: int = 40  # 流式回写攒批粒度，避免打爆网关频率限制
    im_reply_timeout_s: float = 60.0  # 硬超时兜底（FR-4-09）

    @field_validator("wecom_allowed_user_ids", mode="before")
    @classmethod
    def parse_user_id_list(cls, value: object) -> object:
        """.env 中白名单允许两种写法：JSON 数组（`["a","b"]`）或逗号分隔（`a,b`）。

        pydantic-settings 对复杂类型只接受 JSON；逗号分隔是给人手改 `.env`
        用的便利写法，在这里归一化成 list 再交给类型校验。
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]

    @model_validator(mode="after")
    def resolve_paths(self) -> Settings:
        """把相对路径锚定到仓库根（CURRENT_PLAN M1 第 1 项）。"""
        for name in _PATH_FIELDS:
            path: Path = getattr(self, name)
            if not path.is_absolute():
                setattr(self, name, REPO_ROOT / path)
        return self

    @model_validator(mode="after")
    def warn_open_whitelist(self) -> Settings:
        """启用 IM 但白名单为空时打 WARNING（TDD §4.1 安全要点）。

        空白名单意味着企业内任何人都能调用机器人、烧 token 配额，
        属可预期但必须显式提醒的配置状态。
        """
        if self.wecom_enabled and not self.wecom_allowed_user_ids:
            logger.warning(
                "WECOM_ENABLED=true 但 WECOM_ALLOWED_USER_IDS 为空："
                "企业内任何成员都可调用小苏，建议配置 userid 白名单"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """进程级单例。FastAPI 依赖注入与脚本入口共用同一份已校验配置。"""
    return Settings()
