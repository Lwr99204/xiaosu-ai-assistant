# `backend/dev_tools/` — 开发期工具（不进运行时）

本目录下的脚本**只在开发/演示阶段手工运行**，不被 `app/` 任何模块 import，
也不会被打进 Docker 镜像。把它们与运行时代码物理隔离，是为了让「生产依赖」
和「开发辅助」的边界一目了然（对应 TDD R17 / ADR-15）。

| 文件 | 何时引入 | 用途 |
|---|---|---|
| `embedding_service.py` | **M0（已就位）** | 本地 BGE-M3 向量化服务，OpenAI 兼容 |
| `gen_seed_docs.py` | M2 | 由 `.md` 真源生成 `.pdf` / `.docx` 语料 |
| `calibrate_threshold.py` | M3 | 检索阈值标定，输出分数分布直方图 |
| `wecom_spike.py` | M9 | 企业微信智能机器人连通性 / 协议实测 |

---

## `embedding_service.py` — 本地 BGE-M3 Embedding 服务

### 为什么需要一个独立服务？

手上这把 LLM key **拿不到任何云端 embedding 能力**（TDD §1.3 实测：
`/embeddings` 三种模型名全 404，`/models` 返回的 23 个模型里没有一个 embedding
模型，同 key 打标准 DashScope 返回 401）。因此 RAG 的向量化环节必须本地化。

直接在 `backend/` 里装 `sentence-transformers` 会把 **torch 2 GB+** 拖进项目
虚拟环境，与「安装快、依赖轻」冲突。**用独立进程隔离依赖是更优架构**，收益：

1. `backend/.venv` 保持轻量（无 torch）
2. embedding **完全离线**，断网也能演示 RAG
3. 服务崩了不影响 backend 启动，故障域清晰

### 出处与模型权重

- **代码出处**：本机已有脚本 `E:\Models\bge-m3-service\app.py` 的入库副本。
  唯一差异是 `MODEL_DIR` / 端口改为可用环境变量覆盖（`BGE_MODEL_DIR` / `BGE_PORT`），
  默认值与原文件一致。详见该文件头部声明。
- **模型权重**：`BAAI/bge-m3`，约 **2.2 GB，不入库**（`.gitignore` 已排除
  `*.bin` / `*.safetensors` / `*.onnx`）。获取方式二选一：
  ```bash
  # 方式 A：huggingface-cli 下载到 E:\Models\bge-m3
  huggingface-cli download BAAI/bge-m3 --local-dir E:\Models\bge-m3
  # 方式 B：本机已有权重时，用环境变量指过去（无需复制）
  export BGE_MODEL_DIR=/path/to/bge-m3
  ```

### 启动与自检

```bash
# 需要 torch / sentence-transformers / fastapi / uvicorn（本服务自带 venv，
# 不要装进 backend/.venv）
python backend/dev_tools/embedding_service.py          # 默认 127.0.0.1:8001

curl -s http://127.0.0.1:8001/health
# {"status":"ok","model":"bge-m3","loaded":true}
```

> ⚠️ `loaded:false` 表示**模型仍在加载**，等几秒重试即可，**不是故障**。
> ⚠️ 首次 embedding 请求含 CUDA 预热，实测 **2.1 s**（之后毫秒级）。
> backend 启动自检时会预热一次，避免第一个提问的用户莫名卡顿。

### 实测基线（2026-11-23，GPU）

| 项 | 实测值 |
|---|---|
| 向量维度 | 1024（`embed_dim`，改动必须重建索引，R8） |
| L2 范数 | 1.0 精确 → 余弦相似度 == 点积，Chroma 用 `cosine` 空间 |
| 批量 4 条 | 278 ms |
| 正确命中 vs 不相关 | 0.71 vs 0.48（`年假有几天` query） |

🔴 **0.48 这个数字最重要**：完全不相关的中文文本之间余弦基线就有 ~0.48，
所以 `retrieval_min_score` 定在 0.35 等于**完全不过滤**，拒答 L1 层形同虚设。
默认值已改为 **0.55**，且以 M3 `calibrate_threshold.py` 的标定结果为准（R16）。
