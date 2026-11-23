#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# acceptance_check.sh — 跑 PRD §7 验收清单中**可自动化**的部分
#
# 状态：🚧 M0 骨架；M7 起逐步填充，M10 完整实现
#
# 覆盖范围（重要）：本脚本只覆盖 **Web API 层**可自动断言的验收项。
# 以下三类**必须人工执行**，脚本无法替代，会在末尾明确列出：
#   · 企业微信真机 10+3 题（DEV_PLAN M9 验收动作 ①~⑯）
#   · Web 后台 4 个页面的交互与引用高亮（M8）
#   · 截图 / 录屏（M12）
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BASE_URL="${BASE_URL:-http://localhost:8000}"
PASSED=0
FAILED=0

pass() { printf '\033[32m  ✅ %s\033[0m\n' "$*"; PASSED=$((PASSED + 1)); }
fail() { printf '\033[31m  ✗ %s\033[0m\n' "$*"; FAILED=$((FAILED + 1)); }
sect() { printf '\n\033[36m%s\033[0m\n' "$*"; }

# ── 步骤 1/4：服务可用性 ──────────────────────────────────────────────
sect "1/4 服务可用性"
if command -v curl >/dev/null 2>&1 && curl -sf --max-time 3 "$BASE_URL/api/health" >/dev/null 2>&1; then
  pass "GET /api/health 可达"
else
  fail "GET /api/health 不可达 —— 请先 bash scripts/start.sh"
  printf '\n\033[31m后端未启动，无法继续自动化验收\033[0m\n'
  exit 1
fi

# ── 步骤 2/4：内部 Mock API（FR-3-04/05）──────────────────────────────
sect "2/4 内部 Mock API"
# M1 启用，逐条断言 HTTP 状态码与关键字段：
#   GET /api/employee/001        → 200，含 name/department
#   GET /api/employee/not-exist  → 404（题目明确要求 404 而非空对象）
#   GET /api/attendance?emp_id=001&start_date=..&end_date=..
#   GET /api/orders?start_date=..&end_date=..  → summary 汇总正确
fail "待 M1 实现"

# ── 步骤 3/4：问答链路（FR-2-xx）──────────────────────────────────────
sect "3/4 问答链路（SSE）"
# M7 启用：
#   POST /api/chat/stream  → 帧序列必须含终止帧（done 或 error），FR-4-09 必达
#   验收题 Q1「员工每年有几天年假？」→ 答案含「10」且 citations 非空
#   验收题 Q8「CEO 家庭住址」→ 必须拒答
#   验收题 Q9「2030 销售目标」→ 必须说不知道
#   多轮：先问 001 部门，再问「他上周上班几天」→ 指代解析正确
#   会话隔离：不同 session_id 不串上下文
fail "待 M7 实现"

# ── 步骤 4/4：文档链路（FR-1-xx 分水岭逻辑）──────────────────────────
sect "4/4 文档上传与增量更新"
# M4 启用，这是**分水岭逻辑**，必须三分支全覆盖：
#   首次上传 → action=created，status: pending→indexed
#   同内容重传 → action=skipped，chunk_count 不变（FR-1-07）
#   改内容重传 → action=replaced，version+1，旧向量被清（FR-1-08）
#   删除后再检索 → 不命中（FR-1-06）
fail "待 M4 实现"

# ── 汇总 ─────────────────────────────────────────────────────────────
printf '\n\033[36m必须人工执行的验收项（脚本无法覆盖）\033[0m\n'
cat <<'MANUAL'
  · 企业微信真机 10+3 题     → DEV_PLAN M9「验收动作」①~⑯
  · Web 后台 4 页交互与高亮  → DEV_PLAN M8「验收动作」
  · 截图 / 录屏              → M12，存 docs/screenshots/
MANUAL

printf '\n'
if [ "$FAILED" -gt 0 ]; then
  printf '\033[31m❌ 自动化验收：%d 通过 / %d 失败\033[0m\n' "$PASSED" "$FAILED"
  exit 1
fi
printf '\033[32m✅ 自动化验收全部通过（%d 项）\033[0m\n' "$PASSED"
