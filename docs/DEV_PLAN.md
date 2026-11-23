# 小苏 · 公司内部 AI 助手 — 开发计划（DEV_PLAN）

> 版本 v1.0 ｜ 状态：**待评审对齐**
> 上游：[PRD.md](./PRD.md)（做什么）· [TDD.md](./TDD.md)（怎么做）｜ 本文档：**按什么顺序做、每步交付什么、怎么提交**

---

## 0. 三条贯穿全程的铁律

在展开里程碑之前，先立三条**不属于任何单一里程碑、但每个里程碑都必须遵守**的规则。这三条直接对应题目的淘汰项与最高分值项。

### 铁律 1：Commit 历史必须健康（淘汰项，7 分）

- **禁止**攒一大堆代码一次性 `git commit -m "init"`。
- 每个里程碑拆成 **2~5 个语义化 commit**，全项目目标 **50+ 个 commit**（§4 给出完整序列，约 53 个；实际开发中的 `fix:` 提交只会更多）。
- 提交信息格式：`<type>(<scope>): <中文描述>`
  - `type` ∈ `feat / fix / docs / test / chore / refactor / perf`
  - 示例：`feat(rag): 实现结构化分块器并保留原文字符偏移`
- **每完成一个可验证的小功能就提交一次**，即使当天只写了 80 行。commit 密度本身就是评分证据。
- 每次 push 前跑一遍 PRD §10.2 的红线自查表。

### 铁律 2：`AI_USAGE.md` 素材必须边做边记（15 分，单独评分）

题目警告「全是 AI 自动生成的官话直接出局」。这份文档**不可能事后凭记忆补写**。执行机制：

- 仓库根建 `docs/ai_journal.md`（**开发期临时素材本，最终删除或折叠进 AI_USAGE.md**）。
- **每完成一个里程碑，立刻追加 3 条记录**，格式固定：
  ```markdown
  ### [M3-2] 分块器偏移量对不上
  - 我给 AI 的原始 prompt：「用 Python 写一个 Markdown 分块函数，按标题切分，每块不超过 600 字符」
  - AI 给的方案：直接 re.split(r'^#{1,6}\s+')，返回文本列表
  - 我发现的问题：re.split 丢弃了分隔符，导致 char_start 全部错位，前端高亮会指到错误位置
  - 我的修正：改用 re.finditer 记录每个标题的 span，切分时同步累加偏移；并加了 8 条单测断言 content[s:e] == chunk.text
  - 结论：AI 擅长写出「能跑」的分块，但不会主动考虑「可追溯性」这类需求——需求得我来提
  ```
- **重点记录 4 类素材**（这是拿高分的关键，不是记录「我用了 AI 很开心」）：
  1. **AI 给的错误/次优方案 + 我如何发现**（最能体现驾驭力）
  2. **我如何拆解任务让 AI 产出可用代码**（prompt 策略）
  3. **AI 完全帮不上、必须我自己搞定的部分**（如钉钉凭证申请、阈值调参的判断）
  4. **我用什么方法验证 AI 的输出**（单测 / Evals / 手工验收题）

### 铁律 3：每个里程碑结束必须「可运行 + 可验证」

- 不允许出现「写了一半、跑不起来」的提交状态。
- 每个里程碑都定义**明确的验收动作**（一条命令或一次手工操作），做完了就当场验。
- `./scripts/test.sh` 从 M1 起就必须能跑通（哪怕只有 1 条测试），**不允许到最后才补测试**。

---

## 1. 里程碑总览

| # | 里程碑 | 主要交付 | 覆盖需求 | commit 数 | 依赖 |
|---|---|---|---|---|---|
| **M0** | 仓库骨架与工程基线 | 目录树、`.gitignore`、`.env.example`、`scripts/*` **骨架**、README 骨架、docs 三件套 | FR-6-03/12/17/18/19（**FR-6-01 只出脚本骨架，不要求真能启动**） | 4 | — |
| **M1** | 后端骨架 + Mock 内部 API | FastAPI 可启动、配置层、日志层、4 个 mock 接口、mock 数据 | FR-3-04/05、FR-6-02/05 | 4 | M0 |
| **M2** | 存储层 + 种子语料 | SQLite 4 表、Repository、7 篇知识库文档 | FR-1-01 数据基础 | 3 | M1 |
| **M3** | **RAG 管道核心** | 加载器、结构化分块（带偏移）、向量化、Chroma 存储、检索 | FR-1-03、FR-2-01 | 5 | M2 |
| **M4** | 文档服务与上传链路 | 上传/列表/删除/重建索引 API、增量更新与去重、异步索引 | FR-1-02/04~09 | 4 | M3 |
| **M5** | LLM 适配层 + MockLLM | Provider 抽象、OpenAI 兼容实现、重试降级、Token 计数、MockLLM | FR-6-07/08/09、FR-2-11 | 4 | M1 |
| **M6** | **工具系统 + Agent 编排** | 工具注册中心、6 个工具、Function Calling 循环、三层拒答 | FR-3-01~03/06~11、FR-2-08/09 | 5 | M3+M5 |
| **M7** | 问答链路打通（Web SSE） | `/api/chat/stream`、SSE 帧协议、会话上下文、多轮记忆 | FR-2-02/04~07/12 | 4 | M6 |
| **M8** | Web 管理后台 | Next.js 4 个页面：文档/日志/设置/调试聊天 | FR-5-01~10 | 5 | M4+M7 |
| **M9** | ⭐ **钉钉 IM 集成** | Stream 长连接、adapter 抽象、会话隔离、兜底话术、Markdown 引用 | **FR-4-01~12/15（15 分 + 淘汰项）** | 4 | M7 |
| **M10** | 工程化收尾与质量门禁 | 测试补齐至 40+、`test.sh`/`lint.sh`/`check_limits.sh` **完整实现**、Docker | **FR-6-01（最终验收）**/09/10/13~18 | 3 | 全部 |
| **M11** | 加分项 | Token 成本展示、多模型验证、飞书第二 adapter、Evals 20+ case | 加分 4 项 | 4 | M10 |
| **M12** | 文档收尾与验收交付 | README 完整版+截图、`AI_USAGE.md`、`自评.md`、`ACCEPTANCE.md`、验收全过 | FR-6-11、AI_USAGE 15 分 | 4 | 全部 |

**总计约 53 个 commit**，远超「不能只有一个 init commit」的要求。

---

## 2. 关键路径与并行安排

### 2.1 关键路径（决定最短工期）

```
M0 → M1 → M2 → M3 → M6 → M7 → M9(钉钉) → M12(验收)
              ↘ M4 ↗        ↗
        M5 ────────────────┘
```

- **最长链**：`M0→M1→M2→M3→M6→M7→M9→M12`。M9（钉钉）在关键路径末端，**这是最大风险点**（15 分 + 淘汰项，且依赖外部凭证）。
- **M5（LLM 层）可与 M3/M4 并行**：它只依赖 M1 的配置层，不依赖 RAG。
- **M8（前端）可与 M9 并行**：都只依赖 M7 打通的问答链路。

### 2.2 ⚠️ 必须立刻并行启动的「人工任务轨道」

这条轨道**不写代码，但阻塞 M9**，必须从 Day 1 就开始推进，不能等代码写完：

| 任务 | 何时做 | 谁做 | 阻塞什么 |
|---|---|---|---|
| **① 确定 LLM 供应商并拿到 API Key** | ✅ **Day 1 已完成** | 你（用户） | ~~M5 之后所有真实联调~~ → 凭证已到位，且 chat / tool_call / stream 三项**实测均 200**（TDD §1.3）。M5 不再被阻塞 |
| **①-b 🔴 补一个 embedding 能力** | **立刻（阻塞 M3）** | 你（用户） | **M3 RAG 全部**。实测该 key **不提供任何 embedding 模型**（`/models` 23 个全量中无一），且打标准 DashScope 返回 **401**。二选一：**方案 A** 去阿里云百炼开一把普通 `sk-` Key 用 `text-embedding-v3`（约 5 分钟）；**方案 B** 改用本地 ONNX `bge-small-zh-v1.5`（我来实现，你无需操作）。详见 TDD §1.4 |
| **② 注册钉钉开发者 → 创建企业内部应用 → 拿 AppKey/AppSecret** | **Day 1~2** | 你（用户） | **M9（15 分 + 淘汰项）** |
| ③ 钉钉后台：开启 Stream 模式、添加机器人能力、配置权限 | Day 2 | 你（用户） | M9 |
| ④ 建一个钉钉测试群、把机器人拉进群 | Day 2 | 你（用户） | M9 验收 |
| ~~⑤ 准备第二个供应商 key（热备 + 多模型加分项）~~ | ❌ **已取消** | — | **不需要了**：实测同一把 token-plan key 就能用 qwen / deepseek / glm 三家共 16 个文本模型，M11 ②「多模型适配」零成本达成 |
| ⑥ （可选）飞书开发者凭证 | M11 前 | 你（用户） | 加分项 |

> 💡 **钉钉凭证获取路径**（无需真实公司）：用个人手机号在钉钉创建「**测试企业/团队**」→ 免费 → 自己就是管理员 → 进 [open-dev.dingtalk.com](https://open-dev.dingtalk.com) → 应用开发 → 企业内部应用 → 创建应用 → 「凭证与基础信息」页拿 **Client ID (AppKey)** 与 **Client Secret (AppSecret)** → 「机器人」能力页开启并选 **Stream 模式** → 发布应用 → 建群把机器人加进去。全程约 20 分钟。

### 2.3 时间盒建议

| 阶段 | 里程碑 | 说明 |
|---|---|---|
| 阶段一：地基 | M0~M2 | 骨架、配置、数据。目标是「跑得起来、有数据可查」 |
| 阶段二：核心能力 | M3、M5、M6 | RAG + LLM + Agent，**项目 70% 的技术含量在这里** |
| 阶段三：链路打通 | M4、M7 | 上传链路 + 问答链路，此时 Web 端已可完整演示 |
| 阶段四：入口与界面 | M8、M9 | 后台 + 钉钉。**M9 是淘汰项，优先级高于 M8** |
| 阶段五：收尾提分 | M10~M12 | 测试、加分项、三份关键文档 |

> **若时间紧张，砍单顺序（从先砍到后砍）**：M11 飞书 adapter → M11 Evals → M8 设置页 → M8 调试聊天页 → M11 Token 成本。
> **绝对不砍**：M9 钉钉、M3 RAG、M6 Agent、M10 测试、M12 三份文档。

---

## 3. 里程碑详情

> 每个里程碑统一给出：**目标 / 任务清单 / 验收动作 / commit 计划 / 需记录的 AI_USAGE 素材**。

### M0 · 仓库骨架与工程基线

**目标**：把「工程规范」这件事一次性做对，后续所有代码都在合规的骨架里生长。

> **范围界定**：M0 交付的是**骨架**，不是可用的启动链路。`scripts/start.sh` / `setup.sh` 等在 M0 只需存在、语法正确、步骤注释齐全；「克隆后一条命令启动」（FR-6-01）的真正验收点在 **M10**。

**任务清单**
- [ ] 确认 GitHub 仓库 `Lwr99204/xiaosu-ai-assistant` 为 **public**，本地 `origin` 指向正确
- [ ] **执行 `git config http.version HTTP/1.1`**（仓库级）—— 本机走 HTTPS + HTTP/2 推送**必现** `Recv failure: Connection was reset`（exit 128），降级到 HTTP/1.1 后立即成功。**已实测验证**，见 TDD R9
- [ ] 建完整目录树（按 TDD §3，含 `docs/screenshots/` 与 `backend/dev_tools/`；`evals/` 可留到 M11 再建），空目录放 `.gitkeep`
- [ ] 写 `.gitignore`：`.env`、`.venv/`、`__pycache__/`、`node_modules/`、`.next/`、`logs/*`（保留 `.gitkeep`）、`storage/`、`*.db`、`.pytest_cache/`、`dist/`、`.DS_Store`
- [ ] 写 `.env.example`：与 `Settings` 字段一一对应，每项带中文注释与示例值（**只放占位符，绝不放真实 key**）
- [ ] 写 `scripts/` **全部 8 个脚本**：`setup.sh`、`start.sh`、`stop.sh`、`test.sh`、`lint.sh`、`seed_data.sh`、`check_limits.sh`、`acceptance_check.sh`。除 `check_limits.sh` 外**此阶段只写骨架**（`#!/usr/bin/env bash` + `set -euo pipefail` + 变量区 + 步骤注释 + `echo` 占位），内容随里程碑填充
  - ⚠️ **FR-6-01「一条命令启动」在 M0 不验收**：M0 只交付脚本骨架，该需求的完整实现与最终验收在 **M10**
  - ⚠️ `scripts/` 由此**达到 8 文件上限**，后续不得再往此目录加脚本（要加就并入现有脚本，或建 `scripts/dev/` 子目录，见 TDD R12）
- [ ] `check_limits.sh` **在 M0 就完整实现**（纯文件遍历、零外部依赖，可立即当门禁用）：
  - 扫描 `backend/**/*.py` 与 `frontend/src/**/*.{ts,tsx}`，行数 >500 报警
  - 扫描每个目录的**直接代码文件数** >8 报警
  - **统计范围限定代码文件**（`.py/.ts/.tsx/.js/.jsx`）；截图、录屏、`.md` 语料、`.pdf/.docx` 等资源与文档不计入（TDD ADR-14），避免 `docs/screenshots/` 触发假阳性
- [ ] 写 `README.md` 骨架：项目名、一句话简介、技术栈表、目录结构、快速开始（占位）、Roadmap（占位）
- [ ] 落定 `docs/PRD.md`、`docs/TDD.md`、`docs/DEV_PLAN.md`（即本三件套）
- [ ] 配置 Git pre-commit 钩子：密钥扫描（正则 `sk-[A-Za-z0-9]{16,}` 等）+ 跑 `check_limits.sh`
- [ ] 建 `docs/ai_journal.md`（铁律 2 的素材本）

**验收动作**
```bash
bash scripts/check_limits.sh   # 输出「✅ 所有文件 ≤500 行，所有目录 ≤8 文件」
git status                     # 确认 .env / .venv 等未被追踪
```

**commit 计划（4 个）**
1. `chore: 初始化仓库骨架与 .gitignore`
2. `docs: 补充 PRD / TDD / 开发计划三份设计文档`
3. `chore(scripts): 添加 setup/start/test/lint/check_limits 脚本骨架`
4. `docs(readme): 补充 README 骨架与环境变量样例`

---

### M1 · 后端骨架 + Mock 内部 API

**目标**：后端能启动、配置能加载、日志能落盘，且题目要求的 3 个 mock 接口可用。

**任务清单**
- [ ] `backend/pyproject.toml`：`uv init`，声明依赖（fastapi / uvicorn / pydantic / pydantic-settings / openai / chromadb / pypdf / python-docx / pytest / pytest-asyncio / httpx / ruff / pytest-socket）
- [ ] `uv sync` 生成 `.venv` 与 `uv.lock`（**确认虚拟环境目录名就是 `.venv`**）
- [ ] `app/config.py`：`Settings` 全量字段（TDD §4.1）
- [ ] `app/logging_setup.py`：`logs/` 轮转 + `request_id` filter + **密钥脱敏 filter**
- [ ] `app/core/`：`errors.py`（异常体系）、`retry.py`（指数退避+抖动）、`ids.py`、`timing.py`
- [ ] `app/main.py`：FastAPI 装配 + lifespan + CORS + `/api/health`
- [ ] `app/api/middleware.py`：request_id 注入、耗时统计、全局异常处理器
- [ ] `app/mock_api/`：`schemas.py`（Employee/AttendanceRecord/Order + Enum）、`repository.py`、`router.py`
- [ ] 生成 `data/mock/*.json`（每个 10-30 条，**必须含边界情况**：迟到/早退/缺卡/深夜加班/请假/退款订单/取消订单/离职员工/同名员工/不存在的 id）
- [ ] 首批测试：**`test_mock_api.py`** ≥5 条 —— 单员工查询、不存在 id → 404、按部门/姓名/日期区间过滤、订单 `summary` 汇总正确。
  - ⚠️ `calculator` 工具属于 **M6** 范畴，**M1 不写 `test_calculator.py`**（避免与本里程碑交付物错位）

**验收动作**
```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
curl http://localhost:8000/api/health                  # {"status":"ok",...}
curl http://localhost:8000/api/employee/001            # 返回张三的信息
curl "http://localhost:8000/api/orders?start_date=2026-11-16&end_date=2026-11-22"
ls logs/                                               # 有 app.log，且内容含 request_id
bash scripts/test.sh                                   # 全绿
```

**commit 计划（4 个）**
1. `feat(backend): 用 uv 初始化后端工程与依赖`
2. `feat(core): 实现配置层、结构化日志与统一异常体系`
3. `feat(mock-api): 实现员工/考勤/订单三个内部 mock 接口`
4. `chore(data): 生成含边界情况的 mock 数据集`

**AI_USAGE 素材**：mock 数据生成（让 AI 造 30 条真实感数据是它擅长的）、异常体系设计（AI 常给过度设计的方案）。

---

### M2 · 存储层 + 种子语料

**目标**：数据落地能力就绪，并准备好用于验证 RAG 的真实语料。

**任务清单**
- [ ] `app/db/schema.sql`：4 张表 DDL（TDD §4.7）
- [ ] `app/db/engine.py`：连接管理（WAL、`check_same_thread=False`）、启动建表、`--init` 命令行入口
- [ ] `app/db/entities.py`：`Document` / `Conversation` / `ToolCall` dataclass
- [ ] `app/db/repositories.py`：`DocumentRepo`（含 `find_by_doc_id` / `upsert` / `update_status` / `list_paged` / `delete`）、`ConversationRepo`（含 `recent_by_session`）、`ToolCallRepo`
- [ ] `app/schemas/`：`chat.py` / `document.py` / `log.py`（前后端契约）
- [ ] **写 7 篇知识库文档**（PRD §6.1）：`员工手册.md`、`新人入职指南.md`、`FAQ.md`、`薪酬福利制度.pdf`、`信息安全规范.docx`、`办公用品申领.txt`、`差旅报销标准.md`
  - ⚠️ **必须包含验收题的确定答案**：年假天数（如 10 天）、报销所需材料清单、入职第一天事项
  - ⚠️ **必须刻意不包含**：CEO 家庭住址、2030 年销售目标（用于验证拒答）
  - Markdown 文档要有清晰的 `#/##/###` 层级（分块器要提取 `heading_path`）
- [ ] `backend/dev_tools/gen_seed_docs.py`：**开发期脚本**（不进运行时）。`.pdf` / `.docx` 无法手写，必须由脚本生成，且**脚本入库**以便评审者复现语料
  - `.docx` 复用 loader 已引入的 `python-docx`；`.pdf` 用 `fpdf2`（`uv add --dev fpdf2`，仅 dev 依赖）+ 一个中文字体文件
  - **语料正文以 `.md` 为唯一真源**，`.pdf/.docx` 由脚本从对应 `.md` 转换生成 → 改内容只改一处，避免三份不同步
  - 生成的 `.pdf/.docx` **入库**（体积小、是验收所需语料）；字体文件不入库，脚本里注明获取方式
- [ ] `scripts/seed_data.sh`：把 `data/knowledge/*` 导入（M4 完成后才真正索引，此步先落盘 + 建 documents 记录）
- [ ] 测试：`test_repositories.py` ≥5 条（CRUD、分页、按 session 取历史、级联删除）

**验收动作**
```bash
cd backend && uv run python -m app.db.engine --init
uv run python -c "from app.db.repositories import DocumentRepo; print(DocumentRepo().count())"
ls data/knowledge/          # 7 个文件
bash scripts/test.sh        # 全绿
```

**commit 计划（3 个）**
1. `feat(db): 实现 SQLite 存储层与 Repository（WAL 模式）`
2. `feat(schemas): 定义前后端共享的 Pydantic 数据契约`
3. `chore(data): 添加初始知识库语料与生成脚本`

**AI_USAGE 素材**：让 AI 生成知识库文档内容（它很擅长编造合理的公司制度），但**验收题答案必须我逐条核对**——AI 生成的「年假 5 天」和验收预期「10 天」不一致会导致后面测试全乱。这是一条很好的「AI 产出必须人工校验」素材。

---

### M3 · RAG 管道核心 ⭐（技术含量最高）

**目标**：文档能进向量库，问题能召回相关片段，且**引用能定位到原文精确位置**。

**任务清单**
- [ ] `app/rag/loaders.py`：4 种格式解析器 + `RawDocument`（含 `content_hash`）+ 编码回退（UTF-8 → GBK → replace）
- [ ] `app/rag/chunking.py`：**结构化分块器**（TDD §4.4.2）
  - 标题栈 → `heading_path`
  - 超长节二次切分（段落 → 句子 → 硬切）
  - `chunk_overlap_chars` 重叠
  - **`char_start` / `char_end` 精确偏移**
  - 过短块合并
- [ ] `app/rag/vector_store.py`：`VectorStore` Protocol + `ChromaStore`（含 `delete_by_doc`）
- [ ] `app/rag/embedder.py`：批量向量化（分批 16）、失败重试 1 次
- [ ] `app/rag/retriever.py`：过采样 top_k×2 → 同源去重（单文档 ≤3 块）→ 相似度归一化 → 阈值过滤
- [ ] `app/rag/citations.py`：`[n]` 解析 → `Citation`（含偏移）+ 越界编号忽略
- [ ] **测试重点**（≥20 条）：
  - `test_chunking.py` 8 条，**核心断言：`raw.content[c.char_start:c.char_end]` 归一化后等于 `c.text`**
  - `test_loaders.py` 5 条、`test_citations.py` 4 条、检索用 MockLLM 的确定性伪向量 3 条

**验收动作**
```bash
cd backend
uv run python -m app.rag.chunking data/knowledge/员工手册.md   # CLI 调试入口，打印分块与 heading_path
uv run python -c "
import asyncio; from app.rag.pipeline import index_file, retrieve
asyncio.run(index_file('data/knowledge/员工手册.md'))
print(asyncio.run(retrieve('员工每年有几天年假')))"              # 应命中 3.1 年假 章节
bash scripts/test.sh
```

**commit 计划（5 个）**
1. `feat(rag): 实现多格式文档加载器与编码回退`
2. `feat(rag): 实现结构化分块器，保留标题路径与原文偏移`
3. `feat(rag): 接入 ChromaDB 并封装 VectorStore 协议`
4. `feat(rag): 实现检索器（过采样/同源去重/阈值过滤）`
5. `test(rag): 补充分块与检索单元测试，校验偏移可回溯`

**AI_USAGE 素材（重点记录，这是最能体现驾驭力的里程碑）**
- AI 初版分块器**丢失字符偏移** → 我发现前端无法高亮 → 改为 `finditer` 记录 span（TDD §4.4.2 已预埋此案例）
- AI 常用 `len(text.split())` 估算中文长度 → **中文没有空格，会严重低估**，必须用 `len(text)`
- AI 给的 Chroma `where` 删除语法在不同版本有差异 → 需查官方文档确认
- 重叠切分导致 `char_start` 落在上一块内部 → 偏移与文本不自洽，需明确「偏移指向本块主体，重叠部分不计入」

---

### M4 · 文档服务与上传链路（分水岭逻辑）

**目标**：跑通「上传 → 索引 → 问答命中」与「删除 → 不再命中」，并做对增量更新。

**任务清单**
- [ ] `app/services/indexing.py`：单 worker 异步任务队列（`asyncio.Queue` + 后台 task），避免并发 embedding 打爆限流
- [ ] `app/services/document_service.py`：
  - `upload()`：校验格式/大小 → SHA-256 → **三分支判定**（created / skipped / replaced，TDD §2.2 流 B）
  - `delete()`：删向量 + 删记录 + 删原文件
  - `reindex()`：手动重建
  - 状态机流转 `pending → indexed / failed`，失败写 `error_message`
- [ ] `app/api/documents.py`：`POST /api/documents`（multipart 多文件，返回 202）、`GET` 列表（分页）、`GET /{doc_id}`、`GET /{doc_id}/content`（前端高亮用）、`DELETE /{doc_id}`、`POST /{doc_id}/reindex`
- [ ] 原文件落盘到 `storage/uploads/{doc_id}`（供原文预览）
- [ ] 路径穿越防御：`secure_filename` + `resolve().is_relative_to()` 校验
- [ ] 测试：`test_document_service.py` 5 条（**同内容重传 skipped**、**改内容 replaced 且 version+1 且旧向量清空**、删除后检索不命中、坏文件 failed 不影响其他文档）

**验收动作**
```bash
# 启动后端，用 curl 模拟上传
curl -F "files=@data/knowledge/员工手册.md" http://localhost:8000/api/documents
curl http://localhost:8000/api/documents            # status 从 pending → indexed
# 再传一次同样的文件
curl -F "files=@data/knowledge/员工手册.md" http://localhost:8000/api/documents   # action: skipped ✅
# 改一个字再传
curl -F "files=@data/knowledge/员工手册.md" http://localhost:8000/api/documents   # action: replaced, version: 2 ✅
bash scripts/test.sh
```

**commit 计划（4 个）**
1. `feat(services): 实现异步索引任务队列`
2. `feat(documents): 实现文档服务与增量更新/去重判定`
3. `feat(api): 添加文档上传、列表、删除与原文预览接口`
4. `test(documents): 覆盖同名替换、内容去重与删除生效场景`

**AI_USAGE 素材**：增量更新的三分支判定逻辑（AI 初版常只做「插入」，忽略同名替换要先删旧向量 → 导致召回重复内容）。

---

### M5 · LLM 适配层 + MockLLM（可与 M3/M4 并行）

**目标**：一套代码接通多家模型供应商，并造出**让测试脱离网络**的 MockLLM。

**任务清单**
- [ ] `app/llm/base.py`：`LLMProvider` Protocol、`ChatMessage`、`ToolCall`、`LLMResult`、`TokenUsage`、`ToolSpec`、`StreamChunk`
- [ ] `app/llm/openai_compat.py`：基于 `openai.AsyncOpenAI(base_url=...)` 实现 `chat()`（含 `tools` / `stream`）与 `embed()`
- [ ] 🔴 **流式请求默认携带 `stream_options={"include_usage": true}`**——实测该端点流式下 `usage` 恒为 `null`，不加则 `total_tokens / cost_cny` **静默落 0**（TDD §1.3 坑 2 / R15）
- [ ] 🔴 **SSE 解析按 delta 字段名分流 `reasoning_content` 与 `content`**——实测 `qwen3.8-max` 是推理模型且 reasoning chunk **先于** content 到达，混拼会把英文思考过程直接吐给用户（TDD §1.3 坑 1 / R14）；`reasoning_content` 映射为 `{type:"reasoning"}` 帧供 Web 折叠展示，IM 侧忽略
- [ ] `app/llm/factory.py`：按 `settings.llm_provider` 构建；**启动时做一次连通性自检**（`chat` 一条 "ping"），失败打印明确诊断（如「API Key 无效」/「base_url 不可达」）而非等到用户提问才炸
- [ ] `app/llm/mock.py`：**MockLLMProvider**（脚本队列 + 确定性伪向量 embed），支持注入异常
- [ ] `app/llm/tokenizer.py`：真实 usage 优先 → `tiktoken` 估算回退 → `PRICING` 单价表折算成本；🔴 **`reasoning_tokens` 必须计入输出侧成本**（实测已含在 `completion_tokens` 内、按输出单价计费）
- [ ] `app/core/retry.py` 接入：仅对超时/5xx/429 重试，指数退避 0.5s→1s + 抖动，最多 2 次；4xx 直接抛
- [ ] `.env.example` 填**实测可用**的供应商配置：token-plan 端点 + `qwen3.8-max`（默认），并把同 key 可用的 `qwen3.8-flash`（快/省）、`deepseek-v4-pro`、`glm-5.3` 作为注释备选——**多模型适配加分项零成本达成，无需第二家 key**
- [ ] `app/api/settings.py`：`GET/PUT /api/settings/model`（FR-5-06，运行时切换模型）
- [ ] 测试：`test_retry.py` 3 条、`test_tokenizer.py` 2 条、`test_llm_factory.py` 2 条
- [ ] 🔴 **测试：`test_stream_usage.py`——断言流式路径 `total_tokens > 0`**，防止 R15 静默归零
- [ ] 🔴 **测试：`test_reasoning_split.py`——构造「reasoning chunk 先到」的假 SSE 流，断言最终答案不含 `reasoning_content`**，防止 R14 回归

**验收动作**
```bash
# 真实 key 联调（凭证已到位，实测 chat / tool_call / stream 均 200）
curl -X PUT http://localhost:8000/api/settings/model -d '{"model_id":"qwen3.8-flash"}' -H 'Content-Type: application/json'
cd backend && uv run python -c "
import asyncio; from app.llm.factory import build_provider
from app.llm.base import ChatMessage
async def m():
    p = build_provider()
    r = await p.chat([ChatMessage(role='user', content='说一个字')])
    print(r.message.content, r.usage)
asyncio.run(m())"
# 断网验证 MockLLM
LLM_PROVIDER=mock bash scripts/test.sh     # 全绿，且 pytest-socket 保证未联网
```

**commit 计划（4 个）**
1. `feat(llm): 定义 LLMProvider 抽象与消息/工具数据结构`
2. `feat(llm): 实现 OpenAI 兼容供应商适配（支持多 base_url 切换）`
3. `feat(llm): 实现 MockLLM 与确定性伪向量，支撑离线测试`
4. `feat(llm): 接入重试退避、Token 计数与成本折算`

**AI_USAGE 素材**：AI 写的 `openai` SDK 调用常混用 v0 与 v1 API（`openai.ChatCompletion.create` 已废弃）；流式 + tool_calls 的增量拼接逻辑（`delta.tool_calls[i].function.arguments` 分片累加）AI 极易写错，需自己读 SDK 源码确认。

---

### M6 · 工具系统 + Agent 编排 ⭐（12 分核心）

**目标**：模型能**自主决定**调哪个工具，调用稳定，且不会乱编答案。

**任务清单**
- [ ] `app/agent/tool_registry.py`：`ToolSpec` / `ToolRegistry` / `to_openai_tools()` / `invoke()`（四步防御，TDD §4.5.1）
- [ ] `app/tools/clock.py`：`get_current_time`（含 `Asia/Shanghai` 默认时区、返回 ISO + 中文星期）
- [ ] `app/tools/calculator.py`：**AST 白名单求值**（禁 `eval`，拒属性访问/函数调用/`__` 开头标识符）
- [ ] `app/tools/internal_api.py`：`query_employee` / `query_attendance` / `query_orders`（走内部 httpx 调本地 mock 路由，保持「真实调用 API」的形态）
- [ ] `app/tools/knowledge.py`：`search_knowledge_base`（调 retriever，把 hits 格式化为 `【参考资料】[n] …`）
- [ ] `app/tools/__init__.py`：`ALL_SPECS` 列表（**新增工具只需在此加一行**）
- [ ] `app/agent/prompts.py`：System Prompt 全文（TDD §7.1）+ `SYSTEM_PROMPT_VERSION` + 当前时间注入
- [ ] `app/agent/refusal.py`：敏感意图正则表 + L1 阈值拒答 + L3 无引用后校验
- [ ] `app/agent/orchestrator.py`：Function Calling 循环（≤300 行）、并行多工具、超轮次降级、流式策略（工具轮非流式 / 末轮流式）
- [ ] `GET /api/tools`：暴露工具清单与 JSON Schema（后台可视化 + 面试演示「工具是模型自主选的」）
- [ ] **测试重点**：
  - `test_tool_registry.py` 4 条（未知工具/JSON 错/校验错/超时）
  - `test_calculator.py` 5 条（含 `__import__('os')` 被拒）
  - **`test_agent_with_mock_llm.py` 8 条** ⭐ 题目硬性要求的 Mock LLM 测试
  - `test_refusal.py` 4 条

**验收动作**
```bash
cd backend && uv run python -m app.agent.cli "员工001是哪个部门的？"      # 调试 CLI
# 日志应显示：tool_call query_employee → 结果 → 最终答案
uv run python -m app.agent.cli "现在几点？"                              # 调 get_current_time
uv run python -m app.agent.cli "我们公司CEO的家庭住址是？"                # 拒答 ✅
curl http://localhost:8000/api/tools | jq '.[].name'                     # 6 个工具
bash scripts/test.sh                                                     # MockLLM 测试全绿
```

**commit 计划（5 个）**
1. `feat(agent): 实现工具注册中心与四步防御式调用`
2. `feat(tools): 实现时间、计算器与内部 API 查询工具`
3. `feat(tools): 实现知识库检索工具与参考资料注入格式`
4. `feat(agent): 实现 Function Calling 编排循环与超轮次降级`
5. `feat(agent): 实现三层拒答机制（阈值/Prompt/无引用后校验）`

**AI_USAGE 素材**
- **AI 会不自觉地写 if-else 路由**（`if "订单" in question: call_orders()`）→ 这正是题目明令禁止的「别用一堆 if-else 写死路由」。我发现后重构为纯 function calling，让模型自己选。
- 工具 `description` 措辞对选择准确率影响极大 → 通过 Evals 迭代（加「必须先调用本工具确定基准日期」后，相对时间类问题正确率显著提升）。
- AI 写的 `calculator` 初版直接用 `eval()` → 安全隐患，改 AST 白名单。

---

### M7 · 问答链路打通（Web SSE）

**目标**：从 HTTP 请求到流式答案 + 引用 + Token 统计的完整链路跑通，多轮记忆生效。

**任务清单**
- [ ] `app/services/agent_service.py`：**统一问答入口** `handle(session_key, text, platform, user_id) -> AsyncIterator[ChatFrame]`
  - 读历史（`agent_history_max_turns`=10）→ 调 orchestrator → 落库 conversations + tool_calls
  - **IM 与 Web 共用此入口**（ADR-07）
- [ ] `app/services/log_service.py`：日志写入、分页查询、筛选、统计
- [ ] `app/api/chat.py`：`POST /api/chat/stream` → `StreamingResponse(media_type="text/event-stream")`，帧协议见 TDD §2.2 流 C
  - **必达终止帧**：`try/finally` 保证异常时也发 `error` 帧（FR-4-09 的 Web 侧对应）
- [ ] `app/api/logs.py`：`GET /api/logs`（分页+筛选）、`GET /api/logs/{id}`（含工具调用链）、`GET /api/logs/stats`
- [ ] 会话隔离落地：Web 侧 `session_id` 由前端生成并透传，与 IM 的 `session_key` 同构
- [ ] `/api/health` 扩展：llm / vector_store / db / im 各组件状态
- [ ] 测试：`test_api_chat.py` 3 条（SSE 帧完整、错误帧、session 隔离）、`test_e2e_acceptance.py` 首批 3 条

**验收动作**
```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"员工每年有几天年假？","session_id":"web-test"}'
# 应看到 delta 帧流 → citations 帧 → usage 帧 → done 帧
# 多轮验证：
curl -N -X POST .../api/chat/stream -d '{"message":"员工001是哪个部门的？","session_id":"web-t2"}'
curl -N -X POST .../api/chat/stream -d '{"message":"他上周来上班几天？","session_id":"web-t2"}'   # 应理解「他」=001 ✅
curl "http://localhost:8000/api/logs?page=1&size=10"     # 刚才两轮都在，含 tool_calls
```

**commit 计划（4 个）**
1. `feat(services): 实现 IM/Web 共用的统一问答服务`
2. `feat(api): 实现 SSE 流式问答接口与帧协议`
3. `feat(api): 实现对话日志查询与统计接口`
4. `test(e2e): 补充多轮上下文与验收题端到端测试`

**AI_USAGE 素材**：SSE 在 FastAPI 里的正确写法（`StreamingResponse` + `yield f"data: {json}\n\n"`）、异步生成器里落库的时机（不能在 yield 之前，否则用户已断开）。

---

### M8 · Web 管理后台（8 分，可与 M9 并行）

**目标**：管理员能上传文档、看对话日志、切模型，并有引用高亮的调试聊天页。

**任务清单**
- [ ] `pnpm create next-app@latest frontend`（TS + Tailwind + App Router + src/ + **ESM，不用 import alias 默认值以外的配置**）→ 校验版本 Next ≥15.4 / React ≥19 / Tailwind ≥4
- [ ] `package.json` 设 `"type": "module"`；`tsconfig.json` 开 `strict: true`
- [ ] `next.config.ts` 配 `rewrites`：`/api/:path*` → `http://localhost:8000/api/:path*`
- [ ] `pnpm dlx shadcn@latest init` + 按需添加组件（Button/Table/Card/Dialog/Badge/Drawer/Select/Tabs/Input）
- [ ] `src/lib/api-types.ts`：由 `/openapi.json` 用 `openapi-typescript` 生成（脚本化进 `setup.sh`）
- [ ] `src/lib/api-client.ts`：类型安全的 fetch 封装，统一错误处理（读 `code`/`message`/`request_id`）
- [ ] `src/lib/sse-client.ts`：`fetch + ReadableStream` 消费 SSE，按 `ChatFrame.type` 分派
- [ ] **`/documents`**：拖拽多文件上传 → 202 → 1.5s 轮询 pending 文档状态 → 徽章流转；表格（名称/大小/状态/分块数/版本/时间）；删除确认；原文预览抽屉
- [ ] **`/logs`**：筛选栏（时间/平台/用户/是否调用工具）+ 分页表格 + 详情侧栏（问答全文、**工具调用链时间线**、引用列表、Token/耗时/成本）
- [ ] **`/settings`**：模型下拉切换（调 `PUT /api/settings/model`）、IM 连接状态卡片
- [ ] **`/chat`**：流式打字机 + **引用角标点击 → 原文高亮抽屉**（用 `char_start/char_end`）+ 工具调用过程折叠面板
- [ ] 顶部导航布局（4 页切换）
- [ ] `pnpm lint` 含 `tsc --noEmit`，纳入 `scripts/lint.sh`

**验收动作**
```bash
bash scripts/start.sh          # 后端 + 前端一起起
# 浏览器打开 http://localhost:3000
#  1) /documents 上传 员工手册.md → 徽章 pending→indexed；再传一次 → 提示「已跳过（内容相同）」
#  2) /chat 问「员工每年有几天年假？」→ 流式输出 → 点 [1] 角标 → 抽屉打开并高亮原文那句 ✅
#  3) /logs 能看到刚才这轮，展开详情能看到 search_knowledge_base 的入参与结果 ✅
#  4) /settings 切换模型 → /chat 再问一次 → /logs 里 model 字段变了 ✅
pnpm --dir frontend lint && pnpm --dir frontend build
```

**commit 计划（5 个）**
1. `feat(frontend): 用 pnpm 初始化 Next.js 15 + Tailwind 4 工程`
2. `feat(frontend): 接入 OpenAPI 类型生成与 API/SSE 客户端`
3. `feat(frontend): 实现文档管理页（上传/状态轮询/删除/预览）`
4. `feat(frontend): 实现对话日志页与工具调用链详情`
5. `feat(frontend): 实现调试聊天页与引用原文高亮`

**AI_USAGE 素材**：Tailwind 4 的配置方式与 v3 完全不同（CSS-first，无 `tailwind.config.js`），AI 经常给 v3 的写法导致样式不生效——这是很好的「AI 知识过时」案例。shadcn/ui 在 React 19 下的 `pnpm dlx shadcn@latest add` 也有版本坑。

---

### M9 · ⭐ 钉钉 IM 集成（15 分 + 淘汰项，**最高优先级**）

**目标**：员工在钉钉群里 @小苏 或私聊，能得到带引用的答案；任何异常都有友好兜底。

> **前置条件**：§2.2 的 ①②③④ 人工任务已完成，`.env` 里有 `DINGTALK_CLIENT_ID` / `DINGTALK_CLIENT_SECRET` / `DINGTALK_ROBOT_CODE`，且测试群里已有机器人。
> **若凭证未就绪**：先用 `MockIMAdapter`（终端交互模拟）把全部逻辑跑通并测完，凭证到手后只换 adapter 实现，**不阻塞开发**。

**任务清单**
- [ ] `app/im/base.py`：`UnifiedMessage`、`IMAdapter` Protocol、`AdapterHealth`、`MessageHandler` 类型
- [ ] `app/im/session.py`：`build_session_key()`（`platform:conversation_id:user_id`，TDD §4.6.2）
- [ ] `app/im/dingtalk.py`：
  - `RobotHandler.process()` → `ChatbotMessage` → `UnifiedMessage`
  - 群聊 `at_users` 校验（非 @ 消息直接忽略，FR-4-05）
  - 剔除 `@小苏 ` 前缀（FR-4-06）
  - `send_reply()`（Markdown）、`send_placeholder()`、`send_fallback()`、`health()`
- [ ] `app/im/formatter.py`：答案 + 引用 → 钉钉 Markdown（TDD §4.6.4 模板）
- [ ] `app/im/runner.py`：
  - 长连接生命周期（在 FastAPI lifespan 中 `asyncio.create_task` 启停）
  - **幂等去重**：内存 LRU(1024) + `processed_messages` 表双保险
  - **占位消息**：收到消息立刻发「🤔 正在查阅…」，Agent 结束后**追加一条**正式答案（FR-4-10 / TDD §4.6.3 方案 A / ADR-13）
    - ⚠️ **实现方式已定稿**：钉钉普通 text/Markdown 消息**不支持编辑已发送内容**，因此**不做原地更新**，明确采用「占位 + 追加」**两条独立消息**
    - 失败场景下用户会看到「🤔 正在查阅…」+「⚠️ 兜底话术」两条消息，这是**已接受的取舍**（优于长时间无响应）
    - 单卡片内流式刷新（钉钉 AI 卡片）体验更好但需额外申请卡片模板 ID，列为 P2 加分项放 **M11**
  - **硬超时** `asyncio.wait_for(..., im_reply_timeout_s=60)`，超时主动发提示（FR-4-09）
  - **异常兜底分级话术**（TDD §4.6.3 表格，FR-4-08）
  - supervisor：连接异常退出后指数退避重启 + 刷新 health
- [ ] `/api/health` 与 `/api/settings` 暴露 IM 连接状态（FR-4-15）
- [ ] `dingtalk_enabled=false` 时**完全跳过**，不影响其他功能启动
- [ ] 测试：`test_session.py` 4 条、`test_im_adapter.py` 4 条（假 SDK 注入消息，断言归一化/去重/兜底被调用）

**验收动作（在真实钉钉里跑 PRD §7 全部 10 题）**
```
① 群里发「今天天气不错」（不 @ 机器人）→ 机器人不响应 ✅
② 群里 @小苏 员工每年有几天年假？ → 收到带「📎 来源」的 Markdown 答案 ✅
③ 私聊小苏 报销发票需要什么材料？ → 正常回答 ✅
④ @小苏 员工001是哪个部门的？ → 调 query_employee ✅
⑤ 紧接着 @小苏 他上周来上班几天？ → 理解「他」=001，调考勤+计算 ✅
⑥ 让另一个账号在同群 @小苏 他是谁？ → 不应接续上一个用户的话题 ✅（会话隔离）
⑦ @小苏 上周一共多少订单？ → 调时间+订单+计算器 ✅
⑧ @小苏 我们公司CEO的家庭住址是？ → 拒答 ✅
⑨ @小苏 2030年的销售目标是多少？ → 说不知道 ✅
⑩ 把 .env 里 LLM_API_KEY 改成 sk-invalid，重启，@小苏 提问
   → 收到「⚠️ 我的模型服务鉴权失败了，请联系管理员检查 API Key 配置。」，不转圈 ✅
⑪ 同一条消息快速重发 3 次 → 只回答一次 ✅（幂等）
⑫ Web /settings 页显示「钉钉：已连接，最后心跳 xx:xx:xx」✅
⑬ 问一个耗时较长的问题 → 应先收到「🤔 正在查阅…」，数秒后再收到正式答案
   → 确认是**两条独立消息**（钉钉不支持编辑已发送消息，符合 ADR-13 定稿方案）✅
```

**commit 计划（4 个）**
1. `feat(im): 定义 IMAdapter 抽象与统一消息模型`
2. `feat(im): 实现会话隔离键与消息格式化`
3. `feat(im): 接入钉钉 Stream 长连接与消息处理`
4. `feat(im): 实现幂等去重、硬超时与分级错误兜底`

**AI_USAGE 素材（这个里程碑素材最丰富）**
- `dingtalk-stream` SDK 是**同步阻塞**模型，AI 常直接 `client.start_forever()` 阻塞掉整个 asyncio 事件循环 → 必须用 `asyncio.to_thread` 或 SDK 的异步入口包装。这是很典型的「AI 不懂运行时模型」案例。
- 钉钉 Markdown 消息对 `\n` 与列表语法的支持有限制，AI 给的标准 Markdown 在钉钉里渲染错乱 → 需实测调整。
- 群聊 `at_users` 字段结构与 `isInAtList` 判断，官方文档描述模糊，AI 给的判断方式不准 → 靠打印原始 payload 反推。

---

### M10 · 工程化收尾与质量门禁

**目标**：把「工程化」这 10 分拿满。**FR-6-01「克隆后一条命令启动」在本里程碑最终验收**——M0 只交付了脚本骨架，此处要把 8 个脚本全部填成真实可用。

**任务清单**
- [ ] 补齐测试至 **40+ 条**，覆盖率 `rag/` 与 `agent/` ≥85%
- [ ] `conftest.py`：强制 `llm_provider="mock"` + `pytest-socket` 禁网 → **机制上保证测试不依赖真实 API**
- [ ] `scripts/test.sh` 完整实现：ruff check → ruff format --check → pytest --cov → check_limits.sh → tsc --noEmit
- [ ] `scripts/lint.sh`、`scripts/setup.sh`、`scripts/start.sh`、`scripts/stop.sh`、`scripts/seed_data.sh` 全部完整实现并在 **Git Bash 下实测通过**
- [ ] `scripts/acceptance_check.sh`：把 PRD §7 验收清单中可自动化的部分（Web API 层）脚本化
- [ ] 全链路 `request_id` 贯通验证（HTTP → service → tool → LLM → 日志）
- [ ] 日志脱敏实测：故意打一条含假 key 的日志，确认被掩码
- [ ] `docker-compose.yml` + 两个 `Dockerfile`（后端用 uv，前端多阶段构建）→ `docker compose up` 实测
- [ ] **`check_limits.sh` 纳入 pre-push 钩子**，超限直接拒绝 push
- [ ] `check_limits.sh` **假阳性复核**：放入若干截图与 `.pdf/.docx` 语料后重跑，确认资源文件未被计入（TDD ADR-14 / R11）
- [ ] 全仓扫描：`git log -p | grep -iE "sk-[A-Za-z0-9]{16,}"` 必须无结果（FR-6-04 淘汰项自查）

**验收动作**
```bash
bash scripts/test.sh                     # 全部门禁通过，输出覆盖率
bash scripts/check_limits.sh             # ✅
# 断网测试（关掉网络后）
bash scripts/test.sh                     # 仍然全绿 ← 证明不依赖真实 API
docker compose up --build                # 两个服务健康
curl http://localhost:8000/api/health
git log -p | grep -icE "sk-[A-Za-z0-9]{16,}"   # 输出 0
```

**commit 计划（3 个）**
1. `test: 补齐单元/集成/端到端测试至 40+ 条并强制离线可跑`
2. `chore(scripts): 完善 setup/start/stop/test/lint/acceptance 脚本`
3. `chore(docker): 添加 Dockerfile 与 compose 一键部署`

---

### M11 · 加分项（**仅在 P0 全部通过后启动**）

**目标**：用最小增量成本拿下 4 个高性价比加分项。

**任务清单（按性价比排序，每项可独立提交、独立砍掉）**
- [ ] **① Token 计数与成本展示**：`/api/logs/stats` 增加当日/累计成本；前端 `/logs` 顶部加统计卡片（数据在 M5/M7 已落库，此处仅做展示）
- [ ] **② 多模型适配验证**：✅ **已实测确认零成本**——同一把 token-plan key 即可用 `qwen3.8-max` / `qwen3.8-flash` / `deepseek-v4-pro` / `glm-5.3`（TDD §1.3）。用 `qwen3.8-flash` 与 `deepseek-v4-pro` 各跑通全部验收题；README 加「切换模型」小节 + 对比表（模型 / 正确率 / 平均耗时 / 单次成本 / 是否推理模型）
- [ ] **③ Evals 自动化评测**：`evals/cases.yaml`（≥20 case）+ `evals/run_evals.py` + 生成 `evals/report.md`；默认跑 Mock 版，`--live` 跑真实模型
  - `evals/` 是**根目录下的新目录**（TDD §3 已登记）。目录**不计入**根目录「6 个文件」的约束，新增它不改变该数字；`evals/` 内 3 个文件也远低于 8 的上限
  - `report.md` 由脚本生成并**入库**，作为加分项的可验证证据
- [ ] **④ 飞书第二 adapter**：`app/im/feishu.py` 实现同一 `IMAdapter` Protocol（长连接模式）；**验证「业务逻辑零改动」**——`git diff --stat` 应只显示新增 adapter 文件与注册行。这个 diff 本身就是加分项的硬证据，截图进 `AI_USAGE.md`
- [ ] **⑤（可选）钉钉 AI 卡片流式输出**：打字机效果
- [ ] **⑥（可选）可观测性**：调用链汇总表 / 简单 metrics 端点

**验收动作**
```bash
uv run python evals/run_evals.py                    # Mock 版：输出通过率报告
uv run python evals/run_evals.py --live             # 真实模型版（需 key）
FEISHU_ENABLED=true bash scripts/start.sh           # 飞书跑通同样 10 题
git diff --stat HEAD~1                              # 证明新增平台只改了 adapter
```

**commit 计划（4 个）**
1. `feat(cost): 后台展示 Token 消耗与成本估算`
2. `feat(evals): 添加 20+ case 自动化评测与报告生成`
3. `feat(im): 接入飞书长连接作为第二个 IM adapter`
4. `docs: 补充多模型对比与切换说明`

---

### M12 · 文档收尾与验收交付

**目标**：三份关键文档达到评分标准，验收清单全过。

**任务清单**
- [ ] **`README.md` 完整版**（逐项对应题目 6.1）：
  - 项目介绍：解决什么问题、面向谁
  - **截图 / 录屏**：钉钉对话截图（含引用来源）、Web 后台 4 页截图、原文高亮截图 → **统一存 `docs/screenshots/`**，README 用相对路径 `docs/screenshots/xxx.png` 引用
    - ⚠️ 截图数量可能达十几张，**不能直接放 `docs/`**（会让 `docs/` 逼近 8 文件上限）；放子目录 `docs/screenshots/` 后，`docs/` 本身仍是 5 个 `.md`
    - GitHub 对仓库内图片有单文件体积限制，截图统一压到 ≤300 KB / 张（`check_limits.sh` 不校验资源，靠自觉）
  - **架构图**：TDD §2.1 图 + 简要说明
  - 安装与启动：前置依赖（uv / pnpm / Node 版本）→ 克隆 → 配 `.env` → `./scripts/setup.sh` → `./scripts/start.sh`；**Windows 用户注明用 Git Bash**
  - 使用说明：钉钉机器人怎么加、后台怎么用、验收 10 题怎么复现
  - 技术栈：表格列出选型 + 版本 + 理由
  - Roadmap：已完成 / 进行中 / 计划中
  - 工程规范说明：≤500 行 / ≤8 文件的约定与自检方式，并**显式写清三条口径**（对应 TDD §3 约定 + ADR-14）：① 只数目录的**直接子文件**，子目录不计入；② `check_limits.sh` **只统计代码文件**（`.py/.ts/.tsx/.js/.jsx`），截图 / 录屏 / 语料 / 文档不计入；③ 根目录文件数为 **6**，`backend/`、`frontend/`、`scripts/`、`data/`、`docs/`、`evals/`、`logs/` 均为目录不计入
  - **可信度声明**：全部自研、未使用 LangChain；若参考过开源实现，显式标注链接与用途
- [ ] **`AI_USAGE.md`**（15 分，**从 `docs/ai_journal.md` 素材提炼，禁止事后编造**）：
  - 我如何使用 AI：分工边界（哪些让 AI 写、哪些必须自己写）、prompt 策略、验证机制
  - **踩坑案例 ≥5 个**，每个含：原始 prompt 摘录 → AI 给的方案 → 问题所在 → 我的修正 → 复盘
    - 选材建议：分块器丢偏移 / 中文长度用 `split()` 估算 / if-else 写死工具路由 / dingtalk-stream 阻塞事件循环 / Tailwind 4 配置写成 v3 / calculator 用 `eval` / 同名文档重复索引
  - AI 帮不上忙的部分：钉钉凭证申请、拒答阈值调参的判断、产品取舍、验收标准理解
  - 我如何验证 AI 产出：单测断言、Evals、手工跑验收 10 题、逐行 review 的标准
- [ ] **`自评.md`（≤1 页）**：
  - 最得意的技术决策 2-3 个（首选：**自研带偏移的分块器** → 支撑「引用可高亮原文位置」；次选：**api/im 平级共用 AgentService** → 结构性保证逻辑复用）
  - 已知不足：无鉴权、SQLite 单机、未做 OCR、阈值需更多语料调优、飞书覆盖度低于钉钉
  - 若继续迭代会怎么做：接 Langfuse 可观测、pgvector + 混合检索（BM25+向量）、Rerank 模型、多租户、真实 SSO
- [ ] **`docs/ACCEPTANCE.md`**：PRD §7 清单 + **实际执行结果勾选**，每题附 `docs/screenshots/` 下的截图相对路径（截图本体放子目录，`docs/` 只保留 5 个 `.md`）
- [ ] 录 30 秒 demo 视频 `docs/screenshots/demo.mp4`（备方案，防现场翻车；README 顶部给出链接）
- [ ] **干净环境全流程验证**：新目录 `git clone` → 照 README 一步步做 → 必须 5 分钟内跑通
- [ ] 最终红线自查（PRD §10.2 全表）+ `git log --oneline` 确认 40+ 提交
- [ ] `git push origin main`；GitHub 页面确认 public、README 渲染正常、截图能显示

**commit 计划（4 个）**
1. `docs(readme): 补全项目介绍、架构图、截图与快速开始`
2. `docs: 编写 AI_USAGE.md（真实踩坑案例与验证方法）`
3. `docs: 编写自评与验收执行记录`
4. `chore: 最终红线自查与发布准备`

---

## 4. Commit 历史总览（淘汰项的直接证据）

预期约 **53 个提交**，每个都对应一个**独立可运行**的状态：

| 里程碑 | 提交序列 |
|---|---|
| **M0** | `chore: 初始化仓库骨架与 .gitignore`<br>`docs: 补充 PRD / TDD / 开发计划三份设计文档`<br>`chore(scripts): 添加 setup/start/test/lint/check_limits 脚本骨架`<br>`docs(readme): 补充 README 骨架与环境变量样例` |
| **M1** | `feat(backend): 用 uv 初始化后端工程与依赖`<br>`feat(core): 实现配置层、结构化日志与统一异常体系`<br>`feat(mock-api): 实现员工/考勤/订单三个内部 mock 接口`<br>`chore(data): 生成含边界情况的 mock 数据集` |
| **M2** | `feat(db): 实现 SQLite 存储层与 Repository（WAL 模式）`<br>`feat(schemas): 定义前后端共享的 Pydantic 数据契约`<br>`chore(data): 添加初始知识库语料与生成脚本` |
| **M3** | `feat(rag): 实现多格式文档加载器与编码回退`<br>`feat(rag): 实现结构化分块器，保留标题路径与原文偏移`<br>`feat(rag): 接入 ChromaDB 并封装 VectorStore 协议`<br>`feat(rag): 实现检索器（过采样/同源去重/阈值过滤）`<br>`test(rag): 补充分块与检索单元测试，校验偏移可回溯` |
| **M4** | `feat(services): 实现异步索引任务队列`<br>`feat(documents): 实现文档服务与增量更新/去重判定`<br>`feat(api): 添加文档上传、列表、删除与原文预览接口`<br>`test(documents): 覆盖同名替换、内容去重与删除生效场景` |
| **M5** | `feat(llm): 定义 LLMProvider 抽象与消息/工具数据结构`<br>`feat(llm): 实现 OpenAI 兼容供应商适配（支持多 base_url 切换）`<br>`feat(llm): 实现 MockLLM 与确定性伪向量，支撑离线测试`<br>`feat(llm): 接入重试退避、Token 计数与成本折算` |
| **M6** | `feat(agent): 实现工具注册中心与四步防御式调用`<br>`feat(tools): 实现时间、计算器与内部 API 查询工具`<br>`feat(tools): 实现知识库检索工具与参考资料注入格式`<br>`feat(agent): 实现 Function Calling 编排循环与超轮次降级`<br>`feat(agent): 实现三层拒答机制（阈值/Prompt/无引用后校验）` |
| **M7** | `feat(services): 实现 IM/Web 共用的统一问答服务`<br>`feat(api): 实现 SSE 流式问答接口与帧协议`<br>`feat(api): 实现对话日志查询与统计接口`<br>`test(e2e): 补充多轮上下文与验收题端到端测试` |
| **M8** | `feat(frontend): 用 pnpm 初始化 Next.js 15 + Tailwind 4 工程`<br>`feat(frontend): 接入 OpenAPI 类型生成与 API/SSE 客户端`<br>`feat(frontend): 实现文档管理页（上传/状态轮询/删除/预览）`<br>`feat(frontend): 实现对话日志页与工具调用链详情`<br>`feat(frontend): 实现调试聊天页与引用原文高亮` |
| **M9** | `feat(im): 定义 IMAdapter 抽象与统一消息模型`<br>`feat(im): 实现会话隔离键与消息格式化`<br>`feat(im): 接入钉钉 Stream 长连接与消息处理`<br>`feat(im): 实现幂等去重、硬超时与分级错误兜底` |
| **M10** | `test: 补齐单元/集成/端到端测试至 40+ 条并强制离线可跑`<br>`chore(scripts): 完善 setup/start/stop/test/lint/acceptance 脚本`<br>`chore(docker): 添加 Dockerfile 与 compose 一键部署` |
| **M11** | `feat(cost): 后台展示 Token 消耗与成本估算`<br>`feat(evals): 添加 20+ case 自动化评测与报告生成`<br>`feat(im): 接入飞书长连接作为第二个 IM adapter`<br>`docs: 补充多模型对比与切换说明` |
| **M12** | `docs(readme): 补全项目介绍、架构图、截图与快速开始`<br>`docs: 编写 AI_USAGE.md（真实踩坑案例与验证方法）`<br>`docs: 编写自评与验收执行记录`<br>`chore: 最终红线自查与发布准备` |

**提交节奏规则**：
- 每完成一个 commit 就 `git push`（小步推送，同时降低网络超时风险）。
- 修 bug 用 `fix(scope):`，重构用 `refactor(scope):`。
- **不用 `--amend` / `rebase` 覆盖已 push 的提交**——历史的真实性本身也是评分点。
- 禁止 `git commit -am "update"` 这类无信息提交。
- 实际开发中允许出现计划外的 `fix:` 提交，**这类提交越多越能体现真实开发过程**，不必刻意压缩。

---

## 5. 每个里程碑结束的固定动作（Checklist）

```
□ bash scripts/test.sh                                   全绿
□ bash scripts/check_limits.sh                           无超限
□ 手工执行该里程碑的「验收动作」，逐条确认
□ git log -p | grep -icE "sk-[A-Za-z0-9]{16,}"           → 必须为 0
□ 往 docs/ai_journal.md 追加 ≥3 条 AI 使用素材（铁律 2）
□ 把本文件对应里程碑任务清单里的 [ ] 改成 [x]
□ git push origin main
```

---

## 6. 立即需要拍板 / 准备的事项

| # | 事项 | 阻塞里程碑 | 紧急度 |
|---|---|---|---|
| 1 | **确认 LLM 供应商并提供 API Key**（建议通义 DashScope：chat + embedding 同一 key） | M5 之后所有真实联调 | 🔴 高 |
| 2 | **钉钉开发者凭证**：AppKey / AppSecret / RobotCode（获取路径见 §2.2） | **M9（15 分 + 淘汰项）** | 🔴 **最高** |
| 3 | 确认 IM 平台选型（钉钉 Stream 模式） | M9 | 🔴 高 |
| 4 | 确认向量库（ChromaDB）与「不用 LangChain、自研 RAG」的决策 | M3 | 🟠 中 |
| 5 | 确认交付 deadline（决定是否启用 §2.3 的砍单顺序） | 全部 | 🔴 高 |
| 6 | 是否需要 Docker 部署路径（影响 M10 工作量） | M10 | 🟡 低 |
| 7 | 是否做飞书第二 adapter（加分项） | M11 | 🟡 低 |

> **确认第 1~5 项后即可进入 M0。**
> 其中第 1、2 项需要你在外部平台亲自操作，**建议现在就开始，与代码开发并行**——这是整个计划中唯一无法靠写代码解决的阻塞点，也是唯一可能导致「淘汰」的外部依赖。

---

## 7. 文档间引用关系

```
招聘笔试题(3).html   ← 唯一事实来源（评分标准）
        │
        ▼
   docs/PRD.md       做什么、做到什么程度（FR/NFR/验收清单/评分映射）
        │
        ▼
   docs/TDD.md       怎么做（选型/架构/模块设计/API契约/测试策略/ADR）
        │
        ▼
  docs/DEV_PLAN.md   按什么顺序做、每步交付什么、怎么提交（本文件）
        │
        ├──► docs/ACCEPTANCE.md   验收执行记录（M12 产出）
        ├──► README.md            面向评审者（M12 产出）
        ├──► AI_USAGE.md          15 分单独评分项（全程积累，M12 成文）
        └──► 自评.md              产品意识与取舍（M12 产出）
```
