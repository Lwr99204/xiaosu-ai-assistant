#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# start.sh — 一条命令启动 Embedding + 后端（含 IM）+ 前端（FR-6-01）
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

# .env 仅在当前进程及子进程中导出；脚本从不打印变量值。
[ -f "$REPO_ROOT/.env" ] || { printf '[start] ✗ 缺少 .env，请先执行 bash scripts/setup.sh\n' >&2; exit 1; }
set -a
# shellcheck disable=SC1091
. "$REPO_ROOT/.env"
set +a

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
EMBED_URL="${EMBED_BASE_URL:-http://127.0.0.1:8001/v1}"
EMBED_PORT="${BGE_PORT:-8001}"
EMBED_HEALTH="http://127.0.0.1:${EMBED_PORT}/health"
HEALTH_URL="http://127.0.0.1:${BACKEND_PORT}/api/health"
RUN_DIR="$REPO_ROOT/storage/run"
LOG_DIR="$REPO_ROOT/logs"

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
  rm -f "$RUN_DIR"/*.pid
  log "已清理"
}
trap cleanup EXIT INT TERM

# ── 步骤 1：前置检查 ──────────────────────────────────────────────────
log "1/5 前置检查 …"
[ -f "$REPO_ROOT/.env" ] || fail "缺少 .env —— 请先执行 bash scripts/setup.sh（会从 .env.example 复制）"
command -v uv >/dev/null 2>&1 || fail "未找到 uv，请先执行 bash scripts/setup.sh"
command -v pnpm >/dev/null 2>&1 || fail "未找到 pnpm，请先执行 bash scripts/setup.sh"
command -v curl >/dev/null 2>&1 || fail "未找到 curl，无法执行健康检查"
mkdir -p "$RUN_DIR" "$LOG_DIR"

# ── 步骤 2：embedding 服务自检（M3 起为硬依赖，R17）──────────────────
# 🔴 不可达时打印**可操作的**启动命令，而不是静默失败让用户去猜
log "2/5 检查本地 embedding 服务 …"
if curl -sf --max-time 3 "$EMBED_HEALTH" >/dev/null 2>&1; then
  log "  ✅ $EMBED_HEALTH 可达"
else
  [ -n "${BGE_MODEL_DIR:-}" ] || fail "Embedding 未运行且未设置 BGE_MODEL_DIR（参见 .env.example）"
  [ -d "$BGE_MODEL_DIR" ] || fail "BGE_MODEL_DIR 不存在：$BGE_MODEL_DIR"
  log "  启动本地 BGE-M3（首次加载可能需要约 1 分钟）…"
  ( cd "$REPO_ROOT/backend" && uv run python dev_tools/embedding_service.py >>"$LOG_DIR/embedding.log" 2>&1 ) &
  PIDS+=("$!")
  printf '%s\n' "$!" >"$RUN_DIR/embedding.pid"
  embed_ready=0
  for _ in $(seq 1 90); do
    if curl -sf --max-time 3 "$EMBED_HEALTH" >/dev/null 2>&1; then embed_ready=1; break; fi
    sleep 1
  done
  [ "$embed_ready" -eq 1 ] || fail "Embedding 启动超时，请查看 logs/embedding.log"
  log "  ✅ Embedding ready"
fi

# ── 步骤 3：后端 ──────────────────────────────────────────────────────
log "3/5 启动后端 :${BACKEND_PORT} …"
if [ -f "$REPO_ROOT/backend/pyproject.toml" ]; then
  ( cd "$REPO_ROOT/backend" && uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" >>"$LOG_DIR/backend.log" 2>&1 ) &
  PIDS+=("$!")
  printf '%s\n' "$!" >"$RUN_DIR/backend.pid"
else
  log "  ⚠ backend 尚未初始化（M1），跳过"
fi

# ── 步骤 4：前端 ──────────────────────────────────────────────────────
log "4/5 启动前端 :${FRONTEND_PORT} …"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
  ( cd "$REPO_ROOT/frontend" && BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}" pnpm dev --hostname 127.0.0.1 --port "$FRONTEND_PORT" >>"$LOG_DIR/frontend.log" 2>&1 ) &
  PIDS+=("$!")
  printf '%s\n' "$!" >"$RUN_DIR/frontend.pid"
else
  log "  ⚠ frontend 尚未初始化（M8），跳过"
fi

# ── 步骤 6/7：轮询健康检查直到 ready ──────────────────────────────────
log "5/5 等待后端就绪 …"
backend_ready=0
for _ in $(seq 1 60); do
  if curl -sf --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then backend_ready=1; break; fi
  sleep 1
done
[ "$backend_ready" -eq 1 ] || fail "后端启动超时，请查看 logs/backend.log"
log "  ✅ 后端 ready"

log ""
log "  后端 API  → $HEALTH_URL"
log "  Web 后台  → http://127.0.0.1:${FRONTEND_PORT}"
log "  IM 长连接 → 由 WECOM_ENABLED 控制，跑在 backend lifespan 内"
log ""

# ── 步骤 8：wait ──────────────────────────────────────────────────────
[ ${#PIDS[@]} -gt 0 ] && wait
