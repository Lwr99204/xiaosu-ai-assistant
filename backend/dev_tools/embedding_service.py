# -*- coding: utf-8 -*-
"""OpenAI 兼容的 BAAI/bge-m3 Embedding 独立服务（开发期工具，不进运行时）。

────────────────────────────────────────────────────────────────────────
出处与副本声明（TDD §1.4 / R17 / ADR-15）
────────────────────────────────────────────────────────────────────────
本文件最初来自本机已有服务 ``E:\\Models\\bge-m3-service\\app.py``，现已补齐
跨机器启动校验与 ONNX 回退，目的是让评审者能复现 RAG 的向量化环节。

模型目录与监听端口由环境变量（``BGE_MODEL_DIR`` / ``BGE_PORT``）提供，包括：
  · ``normalize_embeddings=True``（实测 L2norm=1 → 余弦相似度 == 点积）
  · ``usage.prompt_tokens`` 用 ``len(str(t))`` 冒充 token —— 这是原服务行为，
    **不是真 BPE token**。故 backend 侧 embedding 成本一律记 0 元，
    且绝不可混入 LLM 成本统计，否则 FR-5-10 失真（TDD §1.4 实测表）。

模型权重（约 2.2 GB）**不入库**，获取与放置方式见同目录 README.md。

端点：POST http://127.0.0.1:8001/v1/embeddings   向量维度：1024
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Union

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from tokenizers import Tokenizer

try:
    import torch
    from sentence_transformers import SentenceTransformer
except ModuleNotFoundError:  # 临时开发机可用仓库模型自带的 ONNX 权重，无需下载 Torch
    torch = None
    SentenceTransformer = None

MODEL_DIR = os.environ.get("BGE_MODEL_DIR", "").strip()
MODEL_NAME = "bge-m3"
PORT = int(os.environ.get("BGE_PORT", "8001"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bge-m3-service")

_model: Any = None
_runtime = "unloaded"


def validate_model_dir(model_dir: str) -> Path:
    """拒绝机器相关默认路径，并在加载大模型前给出可操作错误。"""
    if not model_dir:
        raise RuntimeError("缺少 BGE_MODEL_DIR，请在 .env 中填写 BGE-M3 模型目录")
    path = Path(model_dir).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f"BGE_MODEL_DIR 不存在或不是目录：{path}")
    if not (path / "config.json").is_file():
        raise RuntimeError(f"BGE_MODEL_DIR 缺少 config.json：{path}")
    return path


def model_dimension(model: Any) -> int:
    """兼容新旧 sentence-transformers 与轻量 ONNX 包装器。"""
    getter = getattr(model, "get_embedding_dimension", None)
    if getter is None:
        getter = model.get_sentence_embedding_dimension
    return int(getter())


class OnnxSentenceTransformer:
    """只实现本服务所需的 SentenceTransformer 子集。"""

    def __init__(self, model_dir: str) -> None:
        onnx_dir = os.path.join(model_dir, "onnx")
        self._tokenizer = Tokenizer.from_file(os.path.join(onnx_dir, "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()
        self._session = ort.InferenceSession(
            os.path.join(onnx_dir, "model.onnx"),
            providers=["CPUExecutionProvider"],
        )

    @staticmethod
    def get_sentence_embedding_dimension() -> int:
        return 1024

    def encode(self, texts, **_kwargs):
        encoded = self._tokenizer.encode_batch(texts)
        inputs = {
            "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
            "attention_mask": np.asarray(
                [item.attention_mask for item in encoded], dtype=np.int64
            ),
        }
        return self._session.run(["sentence_embedding"], inputs)[0]


class EmbeddingRequest(BaseModel):
    model: str = MODEL_NAME
    input: Union[str, List[str]] = Field(..., description="单条文本或文本列表")
    encoding_format: str = "float"  # ignore other formats


class EmbeddingObject(BaseModel):
    index: int
    object: str = "embedding"
    embedding: List[float]


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: List[EmbeddingObject]
    model: str
    usage: dict


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _model, _runtime
    model_dir = validate_model_dir(MODEL_DIR)
    if SentenceTransformer is not None and torch is not None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("loading model from %s on device=%s", model_dir, device)
        _model = SentenceTransformer(str(model_dir), device=device)
        _runtime = f"sentence-transformers/{device}"
    else:
        onnx_model = model_dir / "onnx" / "model.onnx"
        if not onnx_model.is_file():
            raise RuntimeError(
                "当前 Python 未安装 sentence-transformers，且模型目录没有 onnx/model.onnx；"
                "请设置 BGE_PYTHON 指向 Embedding 专用虚拟环境"
            )
        logger.info("torch unavailable; loading ONNX model from %s on CPU", model_dir)
        _model = OnnxSentenceTransformer(str(model_dir))
        _runtime = "onnxruntime/cpu"
    logger.info("model loaded. dim=%s", model_dimension(_model))
    yield
    _model = None
    _runtime = "unloaded"


app = FastAPI(title="bge-m3 Embedding Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "loaded": _model is not None,
        "dimension": model_dimension(_model) if _model is not None else None,
        "runtime": _runtime,
    }


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    texts = req.input if isinstance(req.input, list) else [req.input]
    if not texts or any((t is None) or (str(t).strip() == "") for t in texts):
        raise HTTPException(status_code=400, detail="input 不能为空")
    try:
        vectors = _model.encode(
            [str(t) for t in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
    except Exception as exc:  # noqa: BLE001
        logger.exception("encode failed")
        raise HTTPException(status_code=500, detail=f"encode failed: {exc}") from exc
    data = [EmbeddingObject(index=i, embedding=vec) for i, vec in enumerate(vectors)]
    n_tokens = sum(len(str(t)) for t in texts)
    return EmbeddingResponse(
        data=data,
        model=MODEL_NAME,
        usage={"prompt_tokens": n_tokens, "total_tokens": n_tokens},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
