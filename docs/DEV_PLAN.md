# 小苏 · 公司内部 AI 助手 — 开发计划（DEV_PLAN）

> 版本 v1.0 ｜ 状态：**待评审对齐**
> 上游：[PRD.md](./PRD.md)（做什么）· [TDD.md](./TDD.md)（怎么做）｜ 本文档：**按什么顺序做、每步交付什么、怎么提交**
>
> ⚠️ **本文件的旧 M0→M12 顺序已停止作为直接施工顺序。** 2026-11-23 起统一按
> [CURRENT_PLAN.md](./CURRENT_PLAN.md) 的 Gate 0→M6 执行；本文件仅保留详细任务素材。

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
  3. **AI 完全帮不上、必须我自己搞定的部分**（如企业微信智能机器人凭证申请、阈值调参的判断）
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
| **M9** | ⭐ **企业微信 IM 集成** | 连通性 spike、智能机器人 WebSocket 长连接、adapter 抽象、会话隔离、**原生流式打字机回复**、兜底话术、Markdown 引用与 4000 字分段 | **FR-4-01~12/15（15 分 + 淘汰项）+ FR-4-14 提前达成** | 5 | M7 |
| **M10** | 工程化收尾与质量门禁 | 测试补齐至 40+、`test.sh`/`lint.sh`/`check_limits.sh` **完整实现**、Docker | **FR-6-01（最终验收）**/09/10/13~18 | 3 | 全部 |
| **M11** | 加分项 | Token 成本展示、多模型验证、飞书第二 adapter、Evals 20+ case | 加分 4 项 | 4 | M10 |
| **M12** | 文档收尾与验收交付 | README 完整版+截图、`AI_USAGE.md`、`自评.md`、`ACCEPTANCE.md`、验收全过 | FR-6-11、AI_USAGE 15 分 | 4 | 全部 |

**总计约 54 个 commit**，远超「不能只有一个 init commit」的要求。

---

## 2. 关键路径与并行安排

### 2.1 关键路径（决定最短工期）

```
M0 → M1 → M2 → M3 → M6 → M7 → M9(企微) → M12(验收)
              ↘ M4 ↗        ↗
        M5 ────────────────┘
```

- **最长链**：`M0→M1→M2→M3→M6→M7→M9→M12`。M9（企业微信）在关键路径末端，**这是最大风险点**（15 分 + 淘汰项，且依赖外部凭证）。
- **M5（LLM 层）可与 M3/M4 并行**：它只依赖 M1 的配置层，不依赖 RAG。
- **M8（前端）可与 M9 并行**：都只依赖 M7 打通的问答链路。

### 2.2 ⚠️ 必须立刻并行启动的「人工任务轨道」

这条轨道**不写代码，但阻塞 M9**，必须从 Day 1 就开始推进，不能等代码写完：

| 任务 | 何时做 | 谁做 | 阻塞什么 |
|---|---|---|---|
| **① 确定 LLM 供应商并拿到 API Key** | ✅ **Day 1 已完成** | 你（用户） | ~~M5 之后所有真实联调~~ → 凭证已到位，且 chat / tool_call / stream 三项**实测均 200**（TDD §1.3）。M5 不再被阻塞 |
| **①-b embedding 能力** | ✅ **已解决 · 无需你操作** | — | ~~阻塞 M3 RAG 全部~~ → **已解除**。复用你本机已有的 **BGE-M3 独立服务**（`http://127.0.0.1:8001/v1/embeddings`，OpenAI 兼容）。实测 `dim=1024`、`L2norm=1`、批量 4 条 278 ms(GPU)、语义排序正确（命中 0.71 vs 不相关 0.48）。**唯一要求**：演示 / 联调前确保该服务已启动——M0 会把服务代码副本收进 `backend/dev_tools/`，backend 启动时自检 `/health` 并给出可操作提示（TDD §1.4 / R17） |
| **② 注册/登录企业微信 → 创建「智能机器人」→ 拿 `bot_id` + `secret`** | **Day 1（🔴 最优先）** | 你（用户） | **M9（15 分 + 淘汰项）** |
| ③ 企业微信后台：确认「智能机器人」功能**是否对本企业开放**、配置可见范围与 `allowedUserIds` 白名单 | Day 1~2 | 你（用户） | M9 ——🔴 **这是 R1 的首要未知项，可能要求企业认证，务必第一时间去点一遍** |
| ④ 建一个企业微信测试群、把智能机器人加进群 | Day 2 | 你（用户） | M9 验收 |
| ~~⑤ 准备第二个供应商 key（热备 + 多模型加分项）~~ | ❌ **已取消** | — | **不需要了**：实测同一把 token-plan key 就能用 qwen / deepseek / glm 三家共 16 个文本模型，M11 ②「多模型适配」零成本达成 |
| ⑥ （可选）飞书开发者凭证 | M11 前 | 你（用户） | 加分项 |

> 💡 **企业微信凭证获取路径**（无需真实公司，对应 TDD §1.5）：用个人手机号在 [work.weixin.qq.com](https://work.weixin.qq.com) 免费注册一个「企业/团队」→ 自己即管理员 → 登录**管理后台** → 找到 **「智能机器人」**（注意：**不是**「应用管理 → 自建应用」，那条路要公网回调 URL，已被否决）→ 创建机器人 → 取 **`bot_id`** 与 **`secret`** 两项 → 配置可见范围/白名单 → 建群把机器人加进去。
>
> 🔴 **两个必须先验证的前置项**（Day 1 就做，别拖到 M9）：
> 1. **「智能机器人」入口是否存在**。该功能可能要求企业完成认证，或对未认证企业不开放。**若你的企业微信后台找不到这个入口，立刻告诉我**——这会直接改变 M9 的技术路线（回退方案：自建应用回调 + 内网穿透，代价是违反「不暴露公网」前提；或全程用 MockAdapter 降级演示）。
> 2. **`secret` 只显示一次**，创建后立即复制保存；它等同密码，只写进本地 `.env`（已被 `.gitignore` 忽略），**绝不进仓库**（NFR-10 / pre-commit 钩子会拦）。
>
> ⚠️ 与钉钉的路径差异：钉钉是「开发者后台 → 企业内部应用 → AppKey/AppSecret + RobotCode（3 项）→ 开启 Stream 模式」；企业微信智能机器人只要 **2 项凭证**，且**不需要**在后台开启任何「模式」开关——长连接由客户端主动发起。

### 2.3 时间盒建议

| 阶段 | 里程碑 | 说明 |
|---|---|---|
| 阶段一：地基 | M0~M2 | 骨架、配置、数据。目标是「跑得起来、有数据可查」 |
| 阶段二：核心能力 | M3、M5、M6 | RAG + LLM + Agent，**项目 70% 的技术含量在这里** |
| 阶段三：链路打通 | M4、M7 | 上传链路 + 问答链路，此时 Web 端已可完整演示 |
| 阶段四：入口与界面 | M8、M9 | 后台 + 企业微信。**M9 是淘汰项，优先级高于 M8** |
| 阶段五：收尾提分 | M10~M12 | 测试、加分项、三份关键文档 |

> **若时间紧张，砍单顺序（从先砍到后砍）**：M11 飞书 adapter → M11 Evals → M8 设置页 → M8 调试聊天页 → M11 Token 成本。
> **绝对不砍**：M9 企业微信、M3 RAG、M6 Agent、M10 测试、M12 三份文档。

---

## 3. 里程碑详情

> 每个里程碑统一给出：**目标 / 任务清单 / 验收动作 / commit 计划 / 需记录的 AI_USAGE 素材**。

### M0 · 仓库骨架与工程基线

**目标**：把「工程规范」这件事一次性做对，后续所有代码都在合规的骨架里生长。

> **范围界定**：M0 交付的是**骨架**，不是可用的启动链路。`scripts/start.sh` / `setup.sh` 等在 M0 只需存在、语法正确、步骤注释齐全；「克隆后一条命令启动」（FR-6-01）的真正验收点在 **M10**。

**任务清单**
- [x] 确认 GitHub 仓库 `Lwr99204/xiaosu-ai-assistant` 为 **public**，本地 `origin` 指向正确
- [x] **执行 `git config http.version HTTP/1.1`**（仓库级）—— 本机走 HTTPS + HTTP/2 推送**必现** `Recv failure: Connection was reset`（exit 128），降级到 HTTP/1.1 后立即成功。**已实测验证**，见 TDD R9
  - ⚠️ **但 HTTP/1.1 必要而不充分**：实测同配置下仍会偶发 `Connection was reset` / `Failed to connect to github.com:443 after 21093 ms`，**原样重试第 2 次即成功**。故约定：**push 失败先自动重试（≤8 次、间隔 6 s）再判定为真失败**，不要一报错就去改配置瞎折腾
  - 🔴 **M0 实测补充：重试循环必须包硬超时**。`git push` 除了快速失败，还会**无限挂死**（数分钟无任何返回），此时「失败后重试」永远不会触发。写法：`timeout 45 git push origin main`，`rc=124` 视为挂死并重试。M0 本轮实测 3 次失败后**第 4 次成功**（`f043c91..bbe6105`），详见 TDD R9 ④
- [x] 建完整目录树（按 TDD §3，含 `docs/screenshots/` 与 `backend/dev_tools/`；`evals/` 可留到 M11 再建），空目录放 `.gitkeep`
- [x] `backend/dev_tools/embedding_service.py`：**把本机已有的 bge-m3 embedding 服务代码收进仓库**（源：`E:\Models\bge-m3-service\app.py`，README 注明出处），使项目可复现。模型权重（2.2 GB）**不入库**，README 写明获取/放置方式（R17 / ADR-15）
- [x] `backend/dev_tools/README.md`：说明 dev_tools 是**开发期工具**、不进运行时，以及 embedding 服务的启动方式与端口 `8001`
- [x] 写 `.gitignore`：`.env`、`.venv/`、`__pycache__/`、`node_modules/`、`.next/`、`logs/*`（保留 `.gitkeep`）、`storage/`、`*.db`、`.pytest_cache/`、`dist/`、`.DS_Store`
- [x] 写 `.env.example`：与 `Settings` 字段一一对应，每项带中文注释与示例值（**只放占位符，绝不放真实 key**）
- [x] 写 `scripts/` **全部 8 个脚本**：`setup.sh`、`start.sh`、`stop.sh`、`test.sh`、`lint.sh`、`seed_data.sh`、`check_limits.sh`、`acceptance_check.sh`。除 `check_limits.sh` 外**此阶段只写骨架**（`#!/usr/bin/env bash` + `set -euo pipefail` + 变量区 + 步骤注释 + `echo` 占位），内容随里程碑填充
  - ⚠️ **FR-6-01「一条命令启动」在 M0 不验收**：M0 只交付脚本骨架，该需求的完整实现与最终验收在 **M10**
  - ⚠️ `scripts/` 由此**达到 8 文件上限**，后续不得再往此目录加脚本（要加就并入现有脚本，或建 `scripts/dev/` 子目录，见 TDD R12）
- [x] `check_limits.sh` **在 M0 就完整实现**（纯文件遍历、零外部依赖，可立即当门禁用）：
  - 扫描 `backend/**/*.py` 与 `frontend/src/**/*.{ts,tsx}`，行数 >500 报警
  - 扫描每个目录的**直接代码文件数** >8 报警
  - **统计范围限定代码文件**（`.py/.ts/.tsx/.js/.jsx`）；截图、录屏、`.md` 语料、`.pdf/.docx` 等资源与文档不计入（TDD ADR-14），避免 `docs/screenshots/` 触发假阳性
- [x] 写 `README.md` 骨架：项目名、一句话简介、技术栈表、目录结构、快速开始（占位）、Roadmap（占位）
- [x] 落定 `docs/PRD.md`、`docs/TDD.md`、`docs/DEV_PLAN.md`（即本三件套）
- [x] 配置 Git pre-commit 钩子：密钥扫描（正则 `sk-[A-Za-z0-9]{16,}`、`wecom.*secret`、`bot_secret`、长度 >20 的疑似 token）+ 跑 `check_limits.sh`
- [x] 建 `docs/ai_journal.md`（铁律 2 的素材本）；🔴 **首批必须补录设计阶段已真实发生的两条**（发生在 M0 之前，别等到 M12 靠回忆编）：① AI 断言「企业微信强制要求公网回调 URL」导致错选钉钉，复核后发现「智能机器人长连接」才是正解；② AI 检索到的 `aibot-python-sdk` 是内容农场仓库（README 18 字节 / `language:null` / 无 license），靠交叉核对才筛出可用的 `wecom-aibot-sdk-python`。详见 DEV_PLAN M9 素材与 TDD §1.5 / R18

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
- [x] `scripts/seed_data.sh`：把 `data/knowledge/*` 解析、分块、向量化并写入 Chroma
- [x] `scripts/seed_data.sh`：通过 DocumentService 同步原文件、documents 状态记录与 Chroma；实测 4 条 indexed / 78 chunks
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

> 编号说明：本节沿用旧 DEV_PLAN 的 M3；在权威 `CURRENT_PLAN.md` 中对应 **M4**。
> 2026-11-24 状态：RAG 核心闭环已完成；文档上传/列表/删除与数据库状态管理仍待后续完成。

**任务清单**
- [x] `app/rag/loaders.py`：4 种格式解析器 + `RawDocument`（含 `content_hash`）+ 编码回退（UTF-8 → GBK → replace）
- [x] `app/rag/chunking.py`：**结构化分块器**（TDD §4.4.2）
  - 标题栈 → `heading_path`
  - 超长节二次切分（段落 → 句子 → 硬切）
  - `chunk_overlap_chars` 重叠
  - **`char_start` / `char_end` 精确偏移**
  - 过短块合并
- [x] `app/rag/vector_store.py`：`VectorStore` Protocol + `ChromaStore`（含 `delete_by_doc`）
  - 🔴 建 collection 时**显式指定 `metadata={"hnsw:space": "cosine"}`**（bge-m3 输出已 L2 归一化，实测 `L2norm=1`，cosine 下 `distance = 1 - cos`）
  - collection 元数据写入 `embed_model` + `embed_dim`，加载时校验；不一致则报错提示需 `reindex`（R8）
- [x] `app/rag/embedder.py`：批量向量化（分批 16，失败重试 1 次，维度由配置读取）
  - 🔴 走**本地 BGE-M3 服务** `http://127.0.0.1:8001/v1/embeddings`（OpenAI 兼容 → 复用 `AsyncOpenAI` **只换 `base_url`**，`api_key` 填占位串 `not-needed`）
  - 🔴 调用前打 `GET /health`：`loaded:false` 表示**模型仍在加载**，应等待重试而非判为故障；端口不可达时打印**可操作**提示（含启动命令）而非静默失败（R17）
  - 🔴 首次请求含 CUDA 预热（实测 **2.1 s**，之后毫秒级）→ 启动自检时**预热一次**，避免第一个提问的用户莫名卡顿
  - ⚠️ 该服务返回的 `usage.prompt_tokens` 实为**字符数**而非真 token（`len(str(t))`）→ embedding 成本一律记 **0 元**，**不得混入** LLM 成本统计，否则 FR-5-10 失真
- [x] `app/rag/retriever.py`：过采样 top_k×2 → 同源去重（单文档 ≤3 块）→ **`score = 1 - cosine_distance`** → 阈值过滤（定稿 **0.63**）
  - 🔴 **弃用原设计的 `1/(1+distance)`**：该变换不可解释、无法与实测余弦基线对齐，调阈值只能靠猜（§4.4.4）
  - 🔴 除绝对阈值外，增加**相对间隔判据** `top1 - mean(top_k) < margin` → 亦判为「检索不可靠」，降低单一阈值的脆弱性（R16）
- [x] 🔴 `backend/dev_tools/calibrate_threshold.py`：**阈值标定脚本**。用真实语料构造「相关对 / 不相关对」两组样本，输出分数分布与建议分界点，据此定稿 `retrieval_min_score`
  - 背景：实测**完全不相关的中文文本之间余弦基线就有 ~0.48**，原设计的 `0.35` **低于噪声地板 = 完全不过滤**，拒答 L1 层形同虚设（R16）
  - ✅ 4 份真实语料最终标定：相关最低 **0.6888**、不相关最高 **0.5741**、建议中点 **0.6314**；配置定稿 `min_score=0.63`、`min_spread=0.01`
- [x] `app/rag/citations.py`：`[n]` 解析 → `Citation`（含偏移）+ 越界编号忽略
- [x] `app/rag/pipeline.py`：`index_file` / `retrieve` 编排；新版本完整写入后切换 active manifest，失败保留旧版本
- [x] **测试重点**（≥20 条）：
  - `test_chunking.py` 8 条，**核心断言：`raw.content[c.char_start:c.char_end]` 归一化后等于 `c.text`**
  - `test_loaders.py`、`test_chunking.py`、`test_embedder.py`、`test_vector_store.py`、
    `test_retriever.py`、`test_citations.py`、`test_pipeline.py` 均已覆盖；全套 **291 passed**

**验收动作**
```bash
# ── 前置：.env 已填写 BGE_MODEL_DIR；标准权重已准备独立 .venv-embedding（见 R17）──
# start 脚本会自动拉起并严格检查 embedding，无需另开终端
.\scripts\start.ps1 -NoPause                      # Windows
# bash scripts/start.sh                            # Linux / macOS
curl -s http://127.0.0.1:8001/health              # 期望 loaded=true、dimension=1024

# ── 主体 ──
cd backend
uv run python -m app.rag.chunking data/knowledge/员工手册.md   # CLI 调试入口，打印分块与 heading_path
uv run python -c "
import asyncio; from app.rag.pipeline import index_file, retrieve
asyncio.run(index_file('data/knowledge/员工手册.md'))
print(asyncio.run(retrieve('员工每年有几天年假')))"              # 应命中员工手册；实测 top1=0.6988，偏移 968:1383 可回溯
uv run python dev_tools/calibrate_threshold.py                 # 打印相关/不相关两组分数分布，据此定稿 retrieval_min_score
bash scripts/test.sh
```

**commit 计划（6 个）**
1. `feat(rag): 实现多格式文档加载器与编码回退`
2. `feat(rag): 实现结构化分块器，保留标题路径与原文偏移`
3. `feat(rag): 接入本地 bge-m3 embedding 服务与 ChromaDB 向量存储`
4. `feat(rag): 实现检索器（过采样 / 同源去重 / 余弦阈值过滤）`
5. `feat(rag): 补充阈值标定脚本，依据真实语料分数分布定稿 min_score`
6. `test(rag): 补充分块与检索单元测试，校验偏移可回溯`

**AI_USAGE 素材（重点记录，这是最能体现驾驭力的里程碑）**
- 🔴 **（已实测发生，最有说服力的一条）AI 在设计阶段把 `retrieval_min_score` 拍成 `0.35`，数值看起来完全合理**；短样本先把起点修正为 `0.55`，再用 4 份真实语料标定出相关最低 **0.6888**、不相关最高 **0.5741**、建议中点 **0.6314**，最终定稿 `0.63`。同时，`min_spread=0.05` 会误杀多个相邻正确块得分接近的 Q1/Q3，实测后降为 `0.01`。**教训：凡是有具体数值的超参，AI 给的都是「看起来合理」的猜测，必须用真实数据标定后才能写死。**
- 🔴 **（已实测发生）AI 默认假设「OpenAI 兼容端点必然同时提供 chat 与 embedding」**，据此把两者绑在同一个 client 上；实测该端点 `/models` 返回的 23 个模型里**没有任何 embedding 模型**（`/embeddings` 三个模型名全 404），且这把 key **不能跨端点复用**（打标准 DashScope 返回 401）→ 架构改为 chat / embed **双 `base_url`**（ADR-15），embed 落到本机 BGE-M3 服务。**教训：供应商能力必须用 `/models` + 真实请求验证，不能靠「兼容 OpenAI 协议」这个名字去推断。**
- 🔴 **（已实测发生）AI 不知道 `qwen3.8-max` 是推理模型**，按普通 chat 模型设计 SSE 解析；实测发现流式下 `delta.reasoning_content` 的 chunk **先于** `delta.content` 到达，若按常规写法无脑拼接，**企业微信里会先吐出一大段英文思考过程**（R14）。同时实测流式下 `usage` **恒为 `null`**，不加 `stream_options={"include_usage": true}` 则成本字段**静默落 0**（R15）。**教训：模型的「隐性行为」（是否推理、流式字段顺序、usage 是否补发）文档里常常不写，只能实测。** 🔁 **平台切换后此坑加重**：IM 改用 `reply_stream` 原生流式后是边生成边推送，思考过程会**实时刷在用户脸上**，不再有「先聚合再过滤」的补救机会。
- AI 初版分块器**丢失字符偏移** → 我发现前端无法高亮 → 改为 `finditer` 记录 span（TDD §4.4.2 已预埋此案例）
- AI 常用 `len(text.split())` 估算中文长度 → **中文没有空格，会严重低估**，必须用 `len(text)`
- AI 给的 Chroma `where` 删除语法在不同版本有差异 → 需查官方文档确认
- 重叠切分导致 `char_start` 落在上一块内部 → 偏移与文本不自洽，需明确「偏移指向本块主体，重叠部分不计入」

---

### M4 · 文档服务与上传链路（分水岭逻辑）

**目标**：跑通「上传 → 索引 → 问答命中」与「删除 → 不再命中」，并做对增量更新。

> 2026-11-24 状态：实现与真实 REST 链路已完成；真实企业微信的上传后问答/删除后拒答
> 留在 CURRENT_PLAN M4 最终验收。

**任务清单**
- [x] `app/services/indexing.py`：单 worker 异步任务队列（`asyncio.Queue` + 后台 task），避免并发 embedding 打爆限流
- [x] `app/services/document_service.py`：
  - `upload()`：校验格式/大小 → SHA-256 → **三分支判定**（created / skipped / replaced，TDD §2.2 流 B）
  - `delete()`：删向量 + 删记录 + 删原文件
  - `reindex()`：手动重建
  - 状态机流转 `pending → indexed / failed`，失败写 `error_message`
- [x] `app/api/documents.py`：`POST /api/documents`（multipart 多文件，返回 202）、`GET` 列表（分页）、`GET /{doc_id}`、`GET /{doc_id}/content`（前端高亮用）、`DELETE /{doc_id}`、`POST /{doc_id}/reindex`
- [x] 原文件按 `storage/uploads/{doc-hash}/{content-hash}/{filename}` 版本化落盘（替换失败时旧原文仍可用）
- [x] 路径穿越防御：basename 归一化 + Windows 非法/保留文件名处理 + `resolve().is_relative_to()` 校验
- [x] 测试：`test_document_service.py` + `test_document_api.py` 覆盖异步状态、去重、替换回滚、串行 worker、删除、手动重建和坏格式；全套 **304 passed**

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

**真实验收记录（2026-11-24，临时隔离存储）**：`常见FAQ.md` 上传立即返回
`pending`，随后变为 `indexed`（27 chunks）；DELETE 返回 `deleted_chunks=27`，列表 total=0。

**commit 计划（4 个）**
1. `feat(services): 实现异步索引任务队列`
2. `feat(documents): 实现文档服务与增量更新/去重判定`
3. `feat(api): 添加文档上传、列表、删除与原文预览接口`
4. `test(documents): 覆盖同名替换、内容去重与删除生效场景`

**AI_USAGE 素材**：增量更新的三分支判定逻辑（AI 初版常只做「插入」，会导致召回重复内容；另一种危险初版是先删旧向量，若新版本索引失败则知识库瞬间不可用）。正确顺序是新版本完整写入 → 原子切换 active manifest → 最后清理旧版本。

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

### M9 · ⭐ 企业微信 IM 集成（15 分 + 淘汰项，**最高优先级**）

**目标**：员工在企业微信群里 @小苏 或私聊，能得到**流式打字机输出**的带引用答案；任何异常都有友好兜底。

> **前置条件**：§2.2 的 ①②③④ 人工任务已完成，`.env` 里有 `WECOM_BOT_ID` / `WECOM_BOT_SECRET`（建议同时配 `WECOM_ALLOWED_USER_IDS`），且测试群里已有智能机器人。
> **若凭证未就绪**：先用 `MockIMAdapter`（终端交互模拟）把全部逻辑跑通并测完，凭证到手后只换 adapter 实现，**不阻塞开发**。
> 🔴 **任务 0（spike）先于一切**：TDD §1.5 已声明——企业微信官方文档是 SPA、正文抓不到，本设计里的协议细节（`reply_stream` 语义、4000 字上限、帧结构）来自**第三方交叉印证而非官方原文**。所以拿到凭证后**第一件事是跑通 SDK 最小示例、把实测结果回写 TDD §1.5**，再动手写 adapter。跳过这步，后面全是空中楼阁。

**任务清单**
- [ ] 🔴 **任务 0 · 连通性 spike**（`backend/dev_tools/wecom_spike.py`，开发期脚本，不进运行时）：装 `wecom-aibot-sdk-python` → 用真实 `bot_id`/`secret` 连 `wss://openws.work.weixin.qq.com` → 收发一条文本 → **实测确认四件事**：① `reply_stream(finish=False/True)` 是「同一条消息原地刷新」还是「追加新消息」；② 单条长度上限是否真为 4000；③ 是否存在频率限制、攒批是否必需；④ `frame` 的真实字段结构（群 ID / userid / message_id / 是否 @ 机器人）。**结果回写 TDD §1.5 与 §4.6.3；与本设计冲突处一律以实测为准**
- [ ] `app/im/base.py`：`UnifiedMessage`、`IMAdapter` Protocol、`AdapterHealth`、`MessageHandler` 类型；🔁 Protocol 新增 **`stream_reply()`**（替代原 `send_placeholder()`，见 ADR-13 改判）
- [ ] `app/im/session.py`：`build_session_key()`（`platform:conversation_id:user_id`，TDD §4.6.2）
- [ ] `app/im/wecom.py`：
  - `WSClient({"bot_id":…, "secret":…})` + `client.on("message.text", handler)` → `frame` → `UnifiedMessage`
  - 群聊「是否 @ 本机器人」校验（非 @ 消息直接忽略，FR-4-05）
  - 剔除 `@小苏 ` 前缀（FR-4-06）
  - **`wecom_allowed_user_ids` 白名单拦截**（TDD §1.5 安全建议）——不在名单内直接忽略并记 WARNING，**不进 Agent、不烧 token**
  - `stream_reply()`（`reply_stream` 打字机）、`send_reply()`（一次性兜底）、`send_fallback()`、`health()`
  - 🔴 **SDK 调用全部收敛在本文件**，其余模块只依赖 `IMAdapter` Protocol —— 这是 R18（社区 SDK 风险）的核心隔离对策
- [ ] `app/im/formatter.py`：答案 + 引用 → 企业微信 Markdown（TDD §4.6.4 模板）；🔴 **按 4000 字符上限在「引用条目边界」分段**，不硬截断字符
- [ ] `app/im/runner.py`：
  - 长连接生命周期（在 FastAPI lifespan 中 `asyncio.create_task` 启停）
  - **幂等去重**：内存 LRU(1024) + `processed_messages` 表双保险
  - 🔁 **流式回复（方案 A，ADR-13 已改判）**：Agent 每产出一段就 `reply_stream(finish=False)`，结束 `finish=True` 收尾——**一条消息内打字机**，不再发两条
    - **攒批**：按 `im_stream_chunk_chars=40` **或** `≥300ms` 取先到者，**禁止逐 token 调用**（会打爆网关频率限制）
    - **工具调用阶段推状态文案**（「🔍 正在查询考勤…」），把 60s 硬超时窗口内的静默期填上
    - ⚠️ **降级路径必须保留**：若 spike 证明 `reply_stream` 实为追加语义，或流式中途抛异常 → 回落「占位 + 追加两条消息」（TDD §4.6.3 方案 B）。**两条路径都要有测试**
    - 🔴 **`reasoning_content` 必须在 Provider 层就分流干净**（R14 已升级为高风险）：流式边生成边可见，思考过程一旦漏出就实时刷在用户脸上，**没有事后过滤的机会**
  - **硬超时** `asyncio.wait_for(..., im_reply_timeout_s=60)`，超时主动发提示（FR-4-09）
  - **异常兜底分级话术**（TDD §4.6.3 表格，FR-4-08）
  - supervisor：SDK **已内置**指数退避重连（1s→…→30s）+ ACK 保序，外层 supervisor 只兜**进程级**异常（连接协程整体崩溃）并刷新 health
- [ ] `/api/health` 与 `/api/settings` 暴露 IM 连接状态（FR-4-15）
- [ ] `wecom_enabled=false` 时**完全跳过**，不影响其他功能启动；`wecom_enabled=true` 但白名单为空 → 打 WARNING
- [ ] 测试：`test_session.py` 4 条、`test_im_adapter.py` 5 条（假 SDK 注入 `frame`，断言归一化 / **白名单拦截** / 去重 / 兜底被调用 / **攒批行为** / `finish=True` 恰好一次）

**验收动作（在真实企业微信里跑 PRD §7 全部 10 题 + 3 项平台专属验证）**
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
⑫ Web /settings 页显示「企业微信：已连接，最后心跳 xx:xx:xx」✅
⑬ 🔁 问一个耗时较长的问题 → 应在**同一条消息内**先看到「🔍 正在查询…」状态文案，
   随后**原地刷新**为完整答案（打字机效果），**全程只有一条消息** ✅（ADR-13 方案 A）
   ⚠️ 若实测显示为两条独立消息 → 说明 reply_stream 是追加语义，回落方案 B 并回写 TDD §1.5
⑭ 🔴 新增：@小苏 问一个会触发推理的复杂问题 → 答案里**不得出现任何英文思考过程** ✅（R14）
⑮ 🔴 新增：用一个**不在白名单**的账号 @小苏 → 机器人不响应，日志有 WARNING ✅
⑯ 🔴 新增：问一个答案很长的文档题 → 引用区块超 4000 字时应**在引用边界分段成多条**，
   而不是被硬截断 ✅
```

**commit 计划（5 个）**
1. `chore(im): 企业微信智能机器人连通性 spike 与协议实测`
2. `feat(im): 定义 IMAdapter 抽象与统一消息模型`
3. `feat(im): 实现会话隔离键、消息格式化与 4000 字分段`
4. `feat(im): 接入企业微信 WebSocket 长连接与流式打字机回复`
5. `feat(im): 实现白名单、幂等去重、硬超时与分级错误兜底`

**AI_USAGE 素材（这个里程碑素材最丰富）**
- 🔴 **（本轮已真实发生，最有说服力的一条）AI 曾断言「企业微信强制要求公网回调 URL，直接排除」**，据此把 IM 选成钉钉；实际复核发现企业微信另有**「智能机器人长连接」**产品线——出向 WebSocket、免公网入口 → 平台改回企业微信，还**顺带解除了**「普通消息不可编辑」这条限制，把打字机流式从 P2 加分项拉进 M9 主线。**教训：AI 对平台能力的判断往往基于「最常见的那条接入路径」，会把局部限制当成全局结论。涉及平台选型必须自己去翻文档的导航结构，确认到底有几条路。**
- 🔴 **（本轮已真实发生）AI 检索到的 Python SDK `aibot-python-sdk` 是内容农场仓库**：描述写得极其专业（"async WebSocket, automatic reconnection, multiple message types, full type hints"），但实际 README 只有 18 字节、GitHub `language` 识别为 `null`、无 license、topics 混着 `whatsapp-automation`/`tensorflow`/`baileys`。**只信描述就会给项目引入一个不可审计的依赖** → 靠交叉核对 star / license / README 实质内容 / 最近 push 时间，才筛出真正可用的 `wecom-aibot-sdk-python`（MIT、已上 PyPI、README 一万字）。
- 🔴 `reply_stream` 的**攒批粒度**AI 不会主动考虑：它倾向每个 token 调一次，会直接打爆网关频率限制。必须自己定「40 字符 / 300ms 取先到」并写测试断言。
- 企业微信 Markdown 对 `\n` 与列表语法的支持可能有限制，AI 给的标准 Markdown 可能渲染错乱 → spike 阶段一并实测调整。
- 群聊「是否 @ 本机器人」的字段结构，官方文档描述模糊（**且本项目根本抓不到正文**）→ 只能靠 spike 打印原始 `frame` 反推。

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
- [ ] **⑤（可选）企业微信进阶能力**：`enter_chat` 入群欢迎语 + `feedback_event` 采集点赞/点踩并回流 Evals 形成反馈闭环（TDD §1.5）。注：**打字机流式输出已在 M9 主线达成**，不再属于加分项
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
  - **截图 / 录屏**：企业微信对话截图（含引用来源、**打字机流式过程**）、Web 后台 4 页截图、原文高亮截图 → **统一存 `docs/screenshots/`**，README 用相对路径 `docs/screenshots/xxx.png` 引用
    - ⚠️ 截图数量可能达十几张，**不能直接放 `docs/`**（会让 `docs/` 逼近 8 文件上限）；放子目录 `docs/screenshots/` 后，`docs/` 本身仍是 5 个 `.md`
    - GitHub 对仓库内图片有单文件体积限制，截图统一压到 ≤300 KB / 张（`check_limits.sh` 不校验资源，靠自觉）
  - **架构图**：TDD §2.1 图 + 简要说明
  - 安装与启动：前置依赖（uv / pnpm / Node 版本）→ 克隆 → 配 `.env` → `./scripts/setup.sh` → `./scripts/start.sh`；**Windows 用户注明用 Git Bash**
  - 使用说明：企业微信智能机器人怎么创建与加群、后台怎么用、验收 10+3 题怎么复现
  - 技术栈：表格列出选型 + 版本 + 理由
  - Roadmap：已完成 / 进行中 / 计划中
  - 工程规范说明：≤500 行 / ≤8 文件的约定与自检方式，并**显式写清三条口径**（对应 TDD §3 约定 + ADR-14）：① 只数目录的**直接子文件**，子目录不计入；② `check_limits.sh` **只统计代码文件**（`.py/.ts/.tsx/.js/.jsx`），截图 / 录屏 / 语料 / 文档不计入；③ 根目录文件数为 **6**，`backend/`、`frontend/`、`scripts/`、`data/`、`docs/`、`evals/`、`logs/` 均为目录不计入
  - **可信度声明**：全部自研、未使用 LangChain；若参考过开源实现，显式标注链接与用途
- [ ] **`AI_USAGE.md`**（15 分，**从 `docs/ai_journal.md` 素材提炼，禁止事后编造**）：
  - 我如何使用 AI：分工边界（哪些让 AI 写、哪些必须自己写）、prompt 策略、验证机制
  - **踩坑案例 ≥5 个**，每个含：原始 prompt 摘录 → AI 给的方案 → 问题所在 → 我的修正 → 复盘
    - 选材建议：分块器丢偏移 / 中文长度用 `split()` 估算 / if-else 写死工具路由 / **误判「企业微信必须公网回调」而错选平台** / **把内容农场 SDK 当成可用依赖** / `reply_stream` 逐 token 调用打爆频率限制 / Tailwind 4 配置写成 v3 / calculator 用 `eval` / 同名文档重复索引
  - AI 帮不上忙的部分：企业微信智能机器人凭证申请（**含确认该功能是否对本企业开放**）、拒答阈值调参的判断、产品取舍、验收标准理解
  - 我如何验证 AI 产出：单测断言、Evals、手工跑验收 10+3 题、逐行 review 的标准
- [ ] **`自评.md`（≤1 页）**：
  - 最得意的技术决策 2-3 个（首选：**自研带偏移的分块器** → 支撑「引用可高亮原文位置」；次选：**api/im 平级共用 AgentService** → 结构性保证逻辑复用）
  - 已知不足：无鉴权、SQLite 单机、未做 OCR、阈值需更多语料调优、飞书 adapter 覆盖度低于企业微信主链路、IM 依赖社区 SDK 而非官方（R18）
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

预期约 **54 个提交**，每个都对应一个**独立可运行**的状态：

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
| **M9** | `chore(im): 企业微信智能机器人连通性 spike 与协议实测`<br>`feat(im): 定义 IMAdapter 抽象与统一消息模型`<br>`feat(im): 实现会话隔离键、消息格式化与 4000 字分段`<br>`feat(im): 接入企业微信 WebSocket 长连接与流式打字机回复`<br>`feat(im): 实现白名单、幂等去重、硬超时与分级错误兜底` |
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
| 1 | ~~确认 LLM 供应商并提供 API Key~~ → ✅ **已完成** | — | ✅ Chat 用阿里云 MaaS token-plan + `qwen3.8-max`（chat/tool_call/stream 三项实测 200）；Embedding 用本机 BGE-M3 服务（TDD §1.3/§1.4） |
| 2 | **企业微信智能机器人凭证**：`bot_id` / `secret`（获取路径见 §2.2）；🔴 **并确认「智能机器人」入口对你的企业是否开放**（可能需企业认证） | **M9（15 分 + 淘汰项）** | 🔴 **最高（唯一未解除的外部阻塞）** |
| 3 | ~~确认 IM 平台选型（钉钉 Stream 模式）~~ → ✅ **已定：企业微信「智能机器人长连接」** | M9 | ✅ 已定（TDD §1.5 / ADR-02 / ADR-17）；钉钉完全移出范围 |
| 4 | 确认向量库（ChromaDB）与「不用 LangChain、自研 RAG」的决策 | M3 | 🟠 中 |
| 5 | 确认交付 deadline（决定是否启用 §2.3 的砍单顺序） | 全部 | 🔴 高 |
| 6 | 是否需要 Docker 部署路径（影响 M10 工作量） | M10 | 🟡 低 |
| 7 | 是否做飞书第二 adapter（加分项） | M11 | 🟡 低 |

> **第 1、3 项已定，第 4、5 项不阻塞 M0 → 可以进入 M0。**
> 🔴 **只剩第 2 项需要你在外部平台亲自操作**：企业微信智能机器人的 `bot_id` / `secret`，以及**确认「智能机器人」入口是否对你的企业开放**（可能要求企业认证）。**建议现在就去后台点一遍，与 M0~M8 的开发并行**——这是整个计划中唯一无法靠写代码解决的阻塞点，也是唯一可能导致「淘汰」的外部依赖。若该入口不存在，请立刻告知，M9 技术路线需要改判（回退方案见 TDD R18 ④）。

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
