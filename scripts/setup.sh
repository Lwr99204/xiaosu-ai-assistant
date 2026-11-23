#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# setup.sh — 一次性初始化开发环境
#
# 状态：🚧 M0 骨架（步骤已注释齐全、可安全执行到占位处）
#       完整实现与「克隆后一条命令启动」的最终验收在 M10（FR-6-01）
#
# 职责：装依赖 → 建库 → 导种子数据 → 装 Git 钩子
# 前置：uv（题目硬性要求）、pnpm、Node ≥20、Git Bash
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
HOOK_SRC="$REPO_ROOT/scripts/dev/pre-commit"
HOOK_DST="$REPO_ROOT/.git/hooks/pre-commit"

log()  { printf '\033[36m[setup]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[setup] ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 步骤 1/6：前置工具检查 ────────────────────────────────────────────
# 缺任何一个都打印**可操作的**安装指引后退出，不要让用户去猜（FR-6-02 精神）
log "1/6 检查前置工具 …"
command -v uv   >/dev/null 2>&1 || fail "未找到 uv。安装：https://docs.astral.sh/uv/ （题目硬性要求用 uv 管理依赖）"
command -v git  >/dev/null 2>&1 || fail "未找到 git。"
# pnpm / node 在 M8 前端引入前不是硬依赖，缺失只警告
command -v pnpm >/dev/null 2>&1 || log "  ⚠ 未找到 pnpm，前端（M8）相关步骤将跳过。安装：npm i -g pnpm"
log "  uv $(uv --version 2>/dev/null || echo '?')"

# ── 步骤 2/6：.env 就位 ───────────────────────────────────────────────
# 只复制不覆盖，避免把用户已填好的真实 key 冲掉
log "2/6 准备 .env …"
if [ ! -f "$REPO_ROOT/.env" ]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  log "  已从 .env.example 创建 .env —— 🔴 请填入 LLM_API_KEY 后再启动"
else
  log "  .env 已存在，跳过（不覆盖）"
fi

# ── 步骤 3/6：后端依赖 ────────────────────────────────────────────────
# uv sync 会自动创建 .venv（目录名强制 .venv）并按 uv.lock 精确还原
log "3/6 安装后端依赖 …"
if [ -f "$BACKEND_DIR/pyproject.toml" ]; then
  ( cd "$BACKEND_DIR" && uv sync )
else
  log "  ⚠ backend/pyproject.toml 尚不存在（M1 引入），跳过"
fi

# ── 步骤 4/6：前端依赖 ────────────────────────────────────────────────
log "4/6 安装前端依赖 …"
if [ -f "$FRONTEND_DIR/package.json" ]; then
  command -v pnpm >/dev/null 2>&1 \
    && ( cd "$FRONTEND_DIR" && pnpm install --frozen-lockfile ) \
    || log "  ⚠ 无 pnpm，跳过"
  # M8：此处追加 openapi-typescript 生成 src/lib/api-types.ts
else
  log "  ⚠ frontend/package.json 尚不存在（M8 引入），跳过"
fi

# ── 步骤 5/6：初始化数据库与种子数据 ──────────────────────────────────
log "5/6 初始化存储 …"
# M2 起启用：
#   ( cd "$BACKEND_DIR" && uv run python -m app.db.engine --init )
#   bash "$REPO_ROOT/scripts/seed_data.sh"
mkdir -p "$REPO_ROOT/storage" "$REPO_ROOT/logs"
log "  storage/ 与 logs/ 已就位（建表与语料导入待 M2）"

# ── 步骤 6/6：安装 Git pre-commit 钩子 ────────────────────────────────
# 钩子做两件事：密钥扫描（FR-6-04 淘汰项）+ 规模门禁（FR-6-17）
log "6/6 安装 Git 钩子 …"
if [ -f "$HOOK_SRC" ] && [ -d "$REPO_ROOT/.git" ]; then
  cp "$HOOK_SRC" "$HOOK_DST"
  chmod +x "$HOOK_DST"
  log "  pre-commit 已安装（密钥扫描 + check_limits）"
else
  log "  ⚠ 未找到钩子源文件或非 git 仓库，跳过"
fi

log "✅ setup 完成"
