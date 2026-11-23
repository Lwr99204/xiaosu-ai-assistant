#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# start.sh — 一条命令启动 后端 + 前端 + IM 长连接（FR-6-01）
#
# 状态：🚧 M0 骨架；完整实现与最终验收在 M10（DEV_PLAN M0 范围界定已明示
#       「M0 不验收 FR-6-01」）。步骤按 TDD §11.1 的 8 步逐条落地。
#
# 设计要点：
#   · 全程**出向连接**，不监听任何公网入站端口（ADR-11 / TDD §11.2）
#   · IM 长连接跑在 FastAPI lifespan 内，随 backend 进程一起起停，
#     不需要第三个进程（TDD §4.6.3）
#   · trap 必须清理全部子进程，否则 Ctrl+C 后残留僵尸 uvicorn / next dev
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
EMBED_URL="${EMBED_BASE_URL:-http://127.0.0.1:8001/v1}"
EMBED_HEALTH="http://127.0.0.1:8001/health"
HEALTH_URL="http://localhost:${BACKEND_PORT}/api/health"

PIDS=()

log()  { printf '\033[36m[start]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[start] ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 步骤 5（TDD §11.1）：trap 清理 ────────────────────────────────────
cleanup() {
  log "正在停止子进程 …"
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  log "已清理"
}
trap cleanup EXIT INT TERM

# ── 步骤 1：前置检查 ──────────────────────────────────────────────────
log "1/5 前置检查 …"
[ -f "$REPO_ROOT/.env" ] || fail "缺少 .env —— 请先执行 bash scripts/setup.sh（会从 .env.example 复制）"
command -v uv >/dev/null 2>&1 || fail "未找到 uv，请先执行 bash scripts/setup.sh"

# ── 步骤 2：embedding 服务自检（M3 起为硬依赖，R17）──────────────────
# 🔴 不可达时打印**可操作的**启动命令，而不是静默失败让用户去猜
log "2/5 检查本地 embedding 服务 …"
if command -v curl >/dev/null 2>&1 && curl -sf --max-time 3 "$EMBED_HEALTH" >/dev/null 2>&1; then
  log "  ✅ $EMBED_HEALTH 可达"
  # ⚠️ loaded:false 表示模型仍在加载，应等待重试而非判为故障（TDD §1.4）
else
  log "  ⚠ $EMBED_HEALTH 不可达 —— RAG 的索引/检索会失败"
  log "    请另开一个终端执行：python backend/dev_tools/embedding_service.py"
  log "    （详见 backend/dev_tools/README.md）"
fi

# ── 步骤 3：后端 ──────────────────────────────────────────────────────
log "3/5 启动后端 :${BACKEND_PORT} …"
if [ -f "$REPO_ROOT/backend/pyproject.toml" ]; then
  ( cd "$REPO_ROOT/backend" && uv run uvicorn app.main:app --reload --port "$BACKEND_PORT" ) &
  PIDS+=("$!")
else
  log "  ⚠ backend 尚未初始化（M1），跳过"
fi

# ── 步骤 4：前端 ──────────────────────────────────────────────────────
log "4/5 启动前端 :${FRONTEND_PORT} …"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
  ( pnpm --dir "$REPO_ROOT/frontend" dev --port "$FRONTEND_PORT" ) &
  PIDS+=("$!")
else
  log "  ⚠ frontend 尚未初始化（M8），跳过"
fi

# ── 步骤 6/7：轮询健康检查直到 ready ──────────────────────────────────
log "5/5 等待后端就绪 …"
# M1 起启用：最多轮询 30 次 × 1s，命中 /api/health 的 status=ok 才算成功；
#            超时则打印 logs/app.log 末尾 20 行辅助定位后退出非 0
# for i in $(seq 1 30); do
#   curl -sf "$HEALTH_URL" >/dev/null 2>&1 && { log "  ✅ ready"; break; }
#   sleep 1
# done
log "  ⚠ 健康检查轮询待 M1 实现"

log ""
log "  后端 API  → $HEALTH_URL"
log "  Web 后台  → http://localhost:${FRONTEND_PORT}"
log "  IM 长连接 → 由 WECOM_ENABLED 控制，跑在 backend lifespan 内（M9）"
log ""

# ── 步骤 8：wait ──────────────────────────────────────────────────────
[ ${#PIDS[@]} -gt 0 ] && wait || log "（M0 阶段无可启动服务，直接退出）"
