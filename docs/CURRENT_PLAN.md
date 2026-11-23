# 小苏当前执行基线（换 AI 必读）

> 更新时间：2026-11-23
> 本文件是当前开发顺序与关键修正的唯一执行基线。发生冲突时，优先级为：
> `招聘笔试题(3).html` → 本文件 → PRD → TDD → 旧 DEV_PLAN。

## 1. 已确定且不得回退的决策

- 主 IM：**企业微信智能机器人长连接**，不再开发钉钉。
- 机器人已由用户改名为“**小苏**”。改名不影响 BotID/Secret。
- `WECOM_BOT_ID` 与 `WECOM_BOT_SECRET` 已从 `E:\ceo-demo\.env` 安全迁移到本项目
  根目录 `.env`；`.env` 已被 Git 忽略，禁止输出或提交真实值。
- 2026-11-23 已完成 Gate 0 实测：WebSocket 连接成功、鉴权成功、心跳成功、收到群文本、
  固定回复成功、收到回复 ACK。
- 后端 Python 3.12 + uv；前端 Next.js 15.4+ / React 19+ / Tailwind 4+ / pnpm。
- Mock API 至少包含员工、考勤、订单；工具路由必须由模型 Function Calling 决定，禁止
  关键词 if-else。

## 2. 已发现的设计修正

后续实现必须遵守，不能照旧 TDD 的冲突段落开发：

1. **引用校验按答案来源执行**：只有调用知识库工具的回答必须带文档引用；员工、考勤、
   订单、时间、计算器等工具答案不得因没有 `[n]` 被拒答。
2. **时间问题必须调用时间工具**：“现在几点、今天、上周”等都调用 `get_current_time`；
   不再每轮把当前时间直接注入 Prompt。
3. **订单接口默认返回明细**：验收题必须出现 `query_orders → calculator`，不能直接使用接口
   预先计算的 summary 冒充 Agent 自主汇总。
4. **多供应商不能虚报**：同一 MaaS Base URL 下切换不同模型只算多模型，不算“≥2 家
   供应商”。只有两个独立 Base URL/凭证真实跑通后才声明该加分项。
5. **拆分 ChatProvider 与 EmbeddingProvider**：二者使用不同端点，不放进一个既 chat 又
   embed 的 Provider 接口。
6. **Schema 修正**：定义独立 `DocumentStatus` 类型；禁止 `DocumentMeta["status"]`；
   `ChatFrame.type` 若保留 reasoning 帧，联合类型必须包含 `reasoning`。
7. **文档替换必须可回滚**：先完成新版本解析和向量写入，成功后切换当前版本并删除旧向量；
   新版本失败时旧文档继续可用。
8. **一键启动必须包含 embedding**：若继续使用本地 BGE-M3，最终 compose/start 脚本必须
   启动并检查 embedding 服务；不能只启动 backend + frontend 就宣称项目可用。
9. **SDK 日志必须脱敏**：企业微信 SDK 默认 DEBUG 会打印原始消息与临时 response URL，
   正式运行必须注入安全 logger，禁止原始帧落盘。
10. **Git 提交自然形成**：一个完整可验证功能一个 commit，目标约 15～25 个有意义提交；
    不要求每写几行就提交，也不人为制造 50+ commit。

## 3. 权威开发顺序

### Gate 0 · 企业微信风险验证（已完成）

- [x] 恢复 `.env.example`，创建本地 `.env`
- [x] 锁定企业微信 SDK 依赖
- [x] 实现 `backend/dev_tools/wecom_spike.py`
- [x] 真实连接、鉴权、收消息、回消息、ACK
- [x] 4 条离线测试通过
- [ ] 正式运行路径注入安全 SDK logger（M1 完成）

### M1 · 后端骨架 + Mock API（当前阶段）

按顺序完成：

1. `app/config.py`：强类型读取 `.env`，路径以仓库根目录解析。
2. `app/logging_setup.py`：`logs/` 轮转、request_id、密钥与临时 URL 脱敏。
3. 企业微信 SDK 安全 logger，禁止原始消息帧进入日志。
4. `app/main.py`、middleware、`GET /api/health`。
5. `app/mock_api/`：员工、考勤、订单明细接口；Pydantic/Enum 强类型。
6. 对接已有 `data/mock/*.json`。
7. ≥5 条 Mock API 测试：员工命中/404、考勤过滤、订单区间、边界状态。
8. 验收：后端可启动、接口可查询、日志落盘无密钥、测试全绿。

### M2 · LLM + 工具系统最小闭环

- ChatProvider、MockLLM、重试与 token 统计。
- 时间、计算器、员工、考勤、订单工具。
- Function Calling 编排，无关键词路由。
- 先跑通 Q4/Q5/Q6 与无效 Key 友好降级。

### M3 · 会话存储 + 企业微信真实 Agent

- SQLite 对话与工具调用日志。
- `platform + conversation_id + user_id` 会话隔离。
- 企业微信 adapter 与 AgentService 共用入口。
- 群聊 @、私聊、多轮、幂等、硬超时与安全日志。
- 在真实企微跑工具题和多轮题，不等项目末期才接 IM。

### M4 · RAG + 文档管理

- 四格式解析、结构化分块、偏移与引用。
- EmbeddingProvider、向量存储、阈值标定、拒答。
- 上传/列表/删除/同名原子替换/去重/异步状态。
- 验收知识库 Q1～Q3、拒答 Q8/Q9、上传删除立即生效。

### M5 · Web 管理后台

- 文档管理、对话日志、工具链、Token、IM 状态。
- 可选调试聊天页；Web 不是员工主入口。

### M6 · 工程化与交付

- 一条命令启动完整依赖，包括 embedding。
- ≥3 条题目硬性测试只是下限，实际覆盖关键链路。
- README、AI_USAGE、自评、验收记录、截图/30 秒视频。
- 干净克隆验证和题目 10+3 项最终验收。
- 全部 P0 通过后才考虑飞书、多供应商、Evals 等加分项。

## 4. 当前仓库状态注意事项

- 工作区含另一台电脑导入的 M1 暂存与未暂存改动，禁止 reset/checkout 覆盖。
- `errors.py`、`ids.py`、`retry.py` 存在 staged 后又修改的状态，提交前要重新核对。
- 四个 `__init__.py` 仍是未跟踪文件。
- 不要把不同阶段的改动一次性混进同一个 commit。
- 每完成阶段必须运行：Ruff、pytest、`scripts/check_limits.sh`、密钥扫描，再提交。

## 5. 给接手 AI 的第一条指令

> 先完整阅读 `docs/招聘笔试题(3).html` 与 `docs/CURRENT_PLAN.md`，再读取当前 Git 状态和
> 未提交代码；保留用户已有改动，从 CURRENT_PLAN 的“当前阶段”继续。旧 PRD/TDD/DEV_PLAN
> 只作为需求和技术细节参考，任何冲突以考题和 CURRENT_PLAN 为准。禁止改用钉钉，禁止把
> 工具答案按“无文档引用”拒答，禁止把同一 Base URL 的不同模型宣称为两家供应商。
