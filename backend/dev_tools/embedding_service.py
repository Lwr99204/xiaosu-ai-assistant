# -*- coding: utf-8 -*-
"""OpenAI 兼容的 BAAI/bge-m3 Embedding 独立服务（开发期工具，不进运行时）。

────────────────────────────────────────────────────────────────────────
出处与副本声明（TDD §1.4 / R17 / ADR-15）
────────────────────────────────────────────────────────────────────────
本文件是本机已有服务 ``E:\\Models\\bge-m3-service\\app.py`` 的**入库副本**，
目的是让评审者能复现 RAG 的向量化环节（原脚本不在仓库内，无法复现）。

与原文件的**唯一差异**：``MODEL_DIR`` 与监听端口改为可用环境变量覆盖
（``BGE_MODEL_DIR`` / ``BGE_PORT``），默认值与原文件完全一致。
除此之外逻辑逐行保持原样，包括：
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

MODEL_DIR = os.environ.get("BGE_MODEL_DIR", r"E:\Models\bge-m3")
MODEL_NAME = "bge-m3"
PORT = int(os.environ.get("BGE_PORT", "8001"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bge-m3-service")

_model: Any = None


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
    global _model
    if SentenceTransformer is not None and torch is not None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("loading model from %s on device=%s", MODEL_DIR, device)
        _model = SentenceTransformer(MODEL_DIR, device=device)
    else:
        logger.info("torch unavailable; loading ONNX model from %s on CPU", MODEL_DIR)
        _model = OnnxSentenceTransformer(MODEL_DIR)
    logger.info("model loaded. dim=%s", _model.get_sentence_embedding_dimension())
    yield
    _model = None


app = FastAPI(title="bge-m3 Embedding Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "loaded": _model is not None}


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
