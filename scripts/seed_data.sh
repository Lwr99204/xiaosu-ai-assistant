#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# seed_data.sh — 生成并导入初始语料与 mock 数据
#
# 状态：🚧 M0 骨架；M2 起逐步填充
#
# 两类数据，来源不同：
#   · data/mock/*.json      —— 员工/考勤/订单，M1 由脚本生成后**入库**
#   · data/knowledge/*      —— 7 篇知识库语料，M2 生成后**入库**
#                              其中 .pdf/.docx 无法手写，必须由
#                              backend/dev_tools/gen_seed_docs.py 从 .md 真源转换
#
# ⚠️ 语料正文以 .md 为**唯一真源**，.pdf/.docx 由脚本生成 → 改内容只改一处
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_DIR="$REPO_ROOT/backend"
MOCK_DIR="$REPO_ROOT/data/mock"
KNOWLEDGE_DIR="$REPO_ROOT/data/knowledge"
GEN_DOCS="$BACKEND_DIR/dev_tools/gen_seed_docs.py"

log()  { printf '\033[36m[seed]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[seed] ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 步骤 1/4：前置检查 ────────────────────────────────────────────────
log "1/4 前置检查 …"
mkdir -p "$MOCK_DIR" "$KNOWLEDGE_DIR"
[ -f "$BACKEND_DIR/pyproject.toml" ] || log "  ⚠ backend 未初始化（M1），后续步骤将跳过"

# ── 步骤 2/4：生成 .pdf / .docx 语料 ──────────────────────────────────
# M2 启用。生成物**入库**（体积小、是验收所需语料）；中文字体文件不入库，
# 获取方式写在 gen_seed_docs.py 的注释里。
log "2/4 从 .md 真源生成 .pdf / .docx …"
if [ -f "$GEN_DOCS" ]; then
  # ( cd "$BACKEND_DIR" && uv run python dev_tools/gen_seed_docs.py )
  log "  ⚠ 调用逻辑待 M2 实现"
else
  log "  ⚠ dev_tools/gen_seed_docs.py 尚不存在（M2 引入），跳过"
fi

# ── 步骤 3/4：校验语料完整性 ──────────────────────────────────────────
# 🔴 这一步是 M2 的关键人工核对点：验收题的答案必须在语料里，
#    且「CEO 家庭住址 / 2030 销售目标」必须**刻意不在**语料里（用于验证拒答）。
log "3/4 校验知识库语料 …"
count="$(find "$KNOWLEDGE_DIR" -maxdepth 1 -type f ! -name '.gitkeep' 2>/dev/null | wc -l | tr -d ' ')"
log "  data/knowledge/ 现有 $count 个文件（M2 目标：7 篇）"
if [ "$count" -lt 7 ]; then
  log "  ⚠ 语料不足 7 篇，验收题 Q1-Q10 将无法全部通过（M2 补齐）"
fi

# ── 步骤 4/4：导入并建索引 ────────────────────────────────────────────
log "4/4 导入数据库 …"
# M2：落盘 + 建 documents 记录（status=pending）
#   ( cd "$BACKEND_DIR" && uv run python -m app.db.engine --init )
# M4 之后：真正触发解析 → 分块 → embedding → 写入 Chroma
#   curl -s -X POST http://localhost:8000/api/documents/reindex-all
log "  ⚠ 导入逻辑待 M2/M4 实现"

log "✅ seed_data 完成（M0 阶段为骨架）"
