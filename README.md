# 小苏 · 公司内部 AI 助手

> 一个**跑在企业微信里**的公司内部 AI 助手：员工 @小苏 提问，它自己决定查知识库、
> 查内部系统还是算数，给出**带原文引用**的答案，答不上来就老实说不知道。

🚧 **当前状态：M0（仓库骨架与工程基线）已完成，功能开发进行中。**
本 README 为骨架版，完整版（含截图、架构图、验收记录）在 M12 交付。

---

## 这个项目解决什么问题

公司内部知识散落在员工手册、报销制度、FAQ 里，员工找信息靠翻文档或问 HR；
考勤、订单等内部系统数据又要登录不同后台才能查。

小苏把这些统一到一个**员工本来就在用的 IM 入口**（企业微信）后面：

- 问「年假有几天」→ 检索知识库，给出答案 **+ 可点击跳转到原文那一行的引用**
- 问「员工 001 上周来了几天」→ 模型**自己决定**调考勤接口、调计算器
- 问「CEO 家庭住址」→ 三层拒答机制挡住，不编造
- 模型服务挂了 → 友好兜底话术，不转圈、不静默

面向两类人：**员工**（企业微信提问）与**管理员**（Web 后台传文档、看日志、切模型）。

---

## 技术栈

| 层次 | 选型 | 版本 | 说明 |
|---|---|---|---|
| 语言 | Python | 3.12 | 后端 |
| 依赖管理 | **uv** | ≥0.12 | 题目硬性要求 |
| Web 框架 | FastAPI + uvicorn | ≥0.115 | 原生 async、自动 OpenAPI |
| 数据校验 | Pydantic v2 + pydantic-settings | ≥2.9 | 强类型配置，启动即校验 |
| LLM 接入 | `openai` SDK（OpenAI 兼容协议） | ≥1.54 | 一套代码切换 qwen / deepseek / glm |
| 默认模型 | `qwen3.8-max`（阿里云 MaaS token-plan） | — | 同 key 可切 4 个模型 |
| Embedding | **本地 BGE-M3 独立服务**（1024 维） | — | 完全离线，见 `backend/dev_tools/` |
| 向量库 | ChromaDB（cosine 空间） | ≥0.5 | 支持按 `doc_id` 删除分块 |
| 关系库 | SQLite（WAL 模式） | stdlib | 零部署 |
| 前端 | **Next.js 15 App Router** + React 19 + Tailwind 4 + shadcn/ui | ≥15.4 | 题目版本底线 |
| 前端包管理 | **pnpm** | ≥11 | 题目硬性要求 |
| IM | **企业微信「智能机器人」WebSocket 长连接** | `wecom-aibot-sdk-python` | 出向连接，**无需公网 IP** |
| 测试 | pytest + pytest-asyncio + httpx + pytest-socket | — | MockLLM + 禁网，断网可跑 |
| 容器 | Docker + compose | — | M10 |

> 🔴 **未使用 LangChain / LlamaIndex**。RAG 管道（加载 → 分块 → 向量化 → 检索 → 引用定位）
> 全部自研，原因是题目要求「引用能定位到原文精确位置」，而框架的 `TextSplitter`
> 默认丢弃字符偏移量。详见 TDD §1.2 ①。

---

## 目录结构

```
xiaosu-ai-assistant/
├── backend/
│   ├── app/               # 运行时代码：api / core / llm / rag / agent /
│   │                      #            tools / im / services / db / schemas / mock_api
│   ├── dev_tools/         # 开发期工具，不进运行时（embedding 服务、语料生成、spike）
│   └── tests/             # unit / integration / e2e 三层
├── frontend/              # Next.js 15（M8 引入）
├── data/
│   ├── knowledge/         # 7 篇初始知识库语料
│   └── mock/              # 员工 / 考勤 / 订单 mock 数据
├── scripts/               # 8 个统一命令入口（已达上限，见下方工程规范）
├── docs/                  # PRD / TDD / DEV_PLAN / ACCEPTANCE / ai_journal
│   └── screenshots/       # 截图与录屏
├── evals/                 # 自动化评测（M11 引入）
├── logs/                  # 运行时日志（不入库）
├── .env.example           # 全量配置项 + 中文注释
├── .gitattributes         # 锁定 .sh 为 LF（Windows 下必需）
└── .gitignore
```

分层依赖是**单向**的，`api/` 与 `im/` 平级且都只调 `services/` —— 这是
「企业微信和 Web 复用同一套问答逻辑」的**结构性保证**，不是口头承诺（TDD §3.1）。

---

## 快速开始

> 🚧 **占位**：M0 只交付脚本骨架，「克隆后一条命令启动」（FR-6-01）的完整实现
> 与最终验收在 **M10**。当前可用的是 `scripts/check_limits.sh`。

**前置依赖**：`uv`、`pnpm`、Node ≥20、Git Bash（Windows 用户必须用 Git Bash 跑 `scripts/*.sh`）

```bash
git clone https://github.com/Lwr99204/xiaosu-ai-assistant.git
cd xiaosu-ai-assistant
cp .env.example .env          # 填入 LLM_API_KEY
bash scripts/setup.sh         # 装依赖 + 建库 + 装 Git 钩子
bash scripts/start.sh         # 起后端 :8000 + 前端 :3000 + IM 长连接
```

**RAG 需要额外的本地 embedding 服务**（M3 起为硬依赖）：

```bash
# 另开一个终端
python backend/dev_tools/embedding_service.py
curl -s http://127.0.0.1:8001/health   # {"status":"ok","model":"bge-m3","loaded":true}
```

模型权重（约 2.2 GB）不入库，获取方式见 [`backend/dev_tools/README.md`](backend/dev_tools/README.md)。

**企业微信智能机器人**（M9）：需在企业管理后台创建「智能机器人」拿到 `bot_id` + `secret`，
填入 `.env` 的 `WECOM_BOT_ID` / `WECOM_BOT_SECRET`，并把 `WECOM_ENABLED` 设为 `true`。
凭证未就绪时保持 `false`，项目其余部分照常运行。

---

## 常用命令

| 命令 | 作用 | 当前状态 |
|---|---|---|
| `bash scripts/setup.sh` | 初始化环境、装依赖、装 Git 钩子 | 🚧 骨架（M10 完整） |
| `bash scripts/start.sh` | 一条命令启动全部服务 | 🚧 骨架（M10 完整） |
| `bash scripts/stop.sh` | 停止服务（按端口兜底清理） | 🚧 骨架（M10 完整） |
| `bash scripts/test.sh` | ruff + pytest + 规模门禁 + tsc | 🚧 骨架（M1 起逐步启用） |
| `bash scripts/lint.sh` | 风格与类型检查 + `.sh` 行尾符自检 | 🚧 骨架（含可用的 LF 检查） |
| `bash scripts/check_limits.sh` | **≤500 行 / ≤8 文件 门禁** | ✅ **已完整实现** |
| `bash scripts/seed_data.sh` | 生成并导入语料与 mock 数据 | 🚧 骨架（M2 起） |
| `bash scripts/acceptance_check.sh` | 跑可自动化的验收项 | 🚧 骨架（M7 起） |

---

## 工程规范（合规底线）

两条硬约束：**单文件 ≤500 行**、**单目录 ≤8 个文件**。由 `scripts/check_limits.sh`
自动校验，并挂在 pre-commit 钩子上。三条统计口径必须说清，否则评审会有歧义：

1. **只数目录的直接子文件**，子目录不计入。
2. **只统计代码文件**（`.py` / `.ts` / `.tsx` / `.js` / `.jsx`）。截图、录屏、`.md` 语料、
   `.pdf` / `.docx` 等资源与文档**不计入** —— 否则 `docs/screenshots/` 放十几张验收截图就误报。
   同理，`__init__.py` 与 `.gitkeep` 不计入目录文件数，但 `__init__.py` **仍受 500 行约束**。
3. **根目录文件数 = 7**：`.env.example`、`.gitignore`、`.gitattributes`、`docker-compose.yml`、
   `README.md`、`AI_USAGE.md`、`自评.md`。`backend/`、`frontend/`、`scripts/`、`data/`、
   `docs/`、`evals/`、`logs/` 都是**目录**，不计入。

⚠️ `scripts/`（8 个）与 `backend/tests/unit/`（8 个）**已达上限**，后续新增必须先建子目录。

**密钥安全**（FR-6-04 是淘汰项）：`.env` 已被 `.gitignore` 忽略；pre-commit 钩子会扫描
暂存内容，命中 `sk-` 开头的真实 key 或「密钥名 = 像真密钥的长值」时**直接拒绝提交**，
且报错信息对密钥做脱敏（只显示首 4 末 2 位）。

---

## Roadmap

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库骨架与工程基线 | ✅ 完成 |
| M1 | 后端骨架 + Mock 内部 API | ⬜ |
| M2 | 存储层 + 种子语料 | ⬜ |
| M3 | RAG 管道核心（分块带偏移 / 检索 / 引用） | ⬜ |
| M4 | 文档服务与上传链路 | ⬜ |
| M5 | LLM 适配层 + MockLLM | ⬜ |
| M6 | 工具系统 + Agent 编排 | ⬜ |
| M7 | 问答链路打通（Web SSE） | ⬜ |
| M8 | Web 管理后台 | ⬜ |
| M9 | ⭐ 企业微信 IM 集成 | ⬜ |
| M10 | 工程化收尾与质量门禁 | ⬜ |
| M11 | 加分项（成本展示 / 多模型 / Evals / 飞书） | ⬜ |
| M12 | 文档收尾与验收交付 | ⬜ |

完整任务拆解与验收动作见 [`docs/DEV_PLAN.md`](docs/DEV_PLAN.md)。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | 做什么、做到什么程度（FR / NFR / 验收清单 / 评分映射） |
| [`docs/TDD.md`](docs/TDD.md) | 怎么做（选型 / 架构 / 模块设计 / API 契约 / 测试策略 / ADR） |
| [`docs/DEV_PLAN.md`](docs/DEV_PLAN.md) | 按什么顺序做、每步交付什么、怎么提交 |
| [`backend/dev_tools/README.md`](backend/dev_tools/README.md) | 开发期工具与本地 embedding 服务 |
| `AI_USAGE.md` | AI 使用方式与踩坑复盘（M12 交付，素材见 `docs/ai_journal.md`） |
| `自评.md` | 技术决策取舍与已知不足（M12 交付） |
