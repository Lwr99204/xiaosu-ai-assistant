#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# test.sh — 一键跑全部门禁（TDD §10.3）
#
# 🔴 铁律 3：本脚本从 M1 起就必须能跑通（哪怕只有 1 条测试），
#    不允许到最后才补测试。
#
# 执行顺序（任一失败即退出非 0）：
#   1. ruff check            静态检查
#   2. ruff format --check   格式检查（只检查不改写）
#   3. pytest --cov          单测 + 集成 + e2e
#   4. check_limits.sh       500 行 / 8 文件 门禁（FR-6-17）
#   5. tsc --noEmit          前端类型检查
#
# 🔴 离线可跑是硬要求（FR-6-09 题目明文）：conftest.py 强制
#    llm_provider="mock" + pytest-socket 禁网，从机制上保证测试不碰真实 API。
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail   # 不用 -e：要跑完所有门禁再汇总，而非首个失败就中断

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
export UV_CACHE_DIR="$REPO_ROOT/.uv-cache"
FAILED=()

UV_BIN="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV_BIN" ] && command -v cygpath >/dev/null 2>&1 && [ -n "${USERPROFILE:-}" ]; then
  uv_candidate="$(cygpath -u "$USERPROFILE")/.local/bin/uv.exe"
  [ -x "$uv_candidate" ] && UV_BIN="$uv_candidate"
fi

step() { printf '\n\033[36m[test] %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ✅ %s\033[0m\n' "$*"; }
bad()  { printf '\033[31m  ✗ %s\033[0m\n' "$*"; FAILED+=("$1"); }

# ── 步骤 1/5：ruff check ──────────────────────────────────────────────
step "1/5 ruff check（静态检查）"
if [ -f "$BACKEND_DIR/pyproject.toml" ]; then
  if [ -z "$UV_BIN" ]; then
    bad "ruff check（未找到 uv）"
  else
    ( cd "$BACKEND_DIR" && "$UV_BIN" run ruff check . ) && ok "ruff check" || bad "ruff check"
  fi
else
  ok "跳过（backend 未初始化）"
fi

# ── 步骤 2/5：ruff format --check ─────────────────────────────────────
step "2/5 ruff format --check（格式检查，不改写文件）"
if [ -n "$UV_BIN" ] && ( cd "$BACKEND_DIR" && "$UV_BIN" run ruff format --check . ); then
  ok "ruff format --check"
else
  bad "ruff format --check"
fi

# ── 步骤 3/5：pytest ──────────────────────────────────────────────────
step "3/5 pytest --cov（单元 / 集成 / e2e）"
if [ -d "$BACKEND_DIR/tests" ] && [ -f "$BACKEND_DIR/pyproject.toml" ]; then
  if [ -z "$UV_BIN" ]; then
    bad "pytest（未找到 uv）"
  elif ( cd "$BACKEND_DIR" && "$UV_BIN" run pytest tests/ \
    --basetemp=.pytest_cache/script_tmp \
    --cov=app/rag --cov=app/agent --cov-report=term-missing ); then
    ok "pytest + rag/agent coverage"
  else
    bad "pytest"
  fi
else
  ok "跳过（tests/ 尚无测试文件 —— M0 只有 .gitkeep）"
fi

# ── 步骤 4/5：工程规模门禁 ────────────────────────────────────────────
# 🔴 这一步 M0 就已真实可跑（check_limits.sh 是 M0 唯一完整实现的脚本）
step "4/5 check_limits.sh（≤500 行 / ≤8 文件）"
if bash "$REPO_ROOT/scripts/check_limits.sh"; then
  ok "工程规模门禁"
else
  bad "工程规模门禁"
fi

# ── 步骤 5/5：前端类型检查 ────────────────────────────────────────────
step "5/5 tsc --noEmit（前端类型检查）"
if [ -f "$FRONTEND_DIR/package.json" ]; then
  if [ ! -x "$FRONTEND_DIR/node_modules/.bin/tsc" ]; then
    bad "前端类型检查（请先安装前端依赖）"
  elif ( cd "$FRONTEND_DIR" && ./node_modules/.bin/tsc --noEmit ); then
    ok "前端类型检查"
  else
    bad "前端类型检查"
  fi
else
  ok "跳过（frontend 未初始化）"
fi

# ── 汇总 ─────────────────────────────────────────────────────────────
printf '\n'
if [ ${#FAILED[@]} -gt 0 ]; then
  printf '\033[31m❌ 门禁未通过：%s\033[0m\n' "${FAILED[*]}"
  exit 1
fi
printf '\033[32m✅ 全部门禁通过\033[0m\n'
