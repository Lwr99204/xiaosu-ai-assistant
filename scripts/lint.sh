#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# lint.sh — 代码风格与类型检查
#
# 与 test.sh 的分工：lint.sh 只做**风格与类型**（快，秒级），
# test.sh 做**行为**（含 pytest 与 check_limits）。
# 提交前想快速自查用本脚本；完整门禁用 scripts/test.sh。
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

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

step() { printf '\n\033[36m[lint] %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ✅ %s\033[0m\n' "$*"; }
bad()  { printf '\033[31m  ✗ %s\033[0m\n' "$*"; FAILED+=("$1"); }

# ── 步骤 1/4：ruff check（后端静态检查）──────────────────────────────
step "1/4 ruff check"
if [ -f "$BACKEND_DIR/pyproject.toml" ]; then
  if [ -z "$UV_BIN" ]; then
    bad "ruff check（未找到 uv）"
  else
    ( cd "$BACKEND_DIR" && "$UV_BIN" run ruff check . ) && ok "ruff check" || bad "ruff check"
  fi
else
  ok "跳过（backend 未初始化）"
fi

# ── 步骤 2/4：ruff format --check ─────────────────────────────────────
# 只检查不改写：CI/钩子里绝不允许自动改文件，否则会产生「本地过、CI 挂」
step "2/4 ruff format --check"
if [ -f "$BACKEND_DIR/pyproject.toml" ]; then
  if [ -z "$UV_BIN" ]; then
    bad "ruff format --check（未找到 uv）"
  else
    ( cd "$BACKEND_DIR" && "$UV_BIN" run ruff format --check . ) \
      && ok "ruff format --check" || bad "ruff format --check"
  fi
else
  ok "跳过（backend 未初始化）"
fi

# ── 步骤 3/4：前端 tsc --noEmit + eslint ──────────────────────────────
step "3/4 前端类型检查（tsc --noEmit）"
if [ -f "$FRONTEND_DIR/package.json" ]; then
  if [ ! -x "$FRONTEND_DIR/node_modules/.bin/tsc" ]; then
    bad "前端类型检查（请先安装前端依赖）"
  else
    ( cd "$FRONTEND_DIR" && ./node_modules/.bin/tsc --noEmit ) \
      && ok "前端类型检查" || bad "前端类型检查"
  fi
else
  ok "跳过（frontend 未初始化）"
fi

# ── 步骤 4/4：shell 脚本行尾符自检 ────────────────────────────────────
# 🔴 这一步 M0 就真实生效。CRLF 的 .sh 在 Git Bash 里 `bash x.sh` 侥幸能跑，
#    但直接 ./x.sh 会因 shebang 带上 \r 报 bad interpreter，Docker/CI 里必挂。
#    .gitattributes 已锁 eol=lf，此处做二次防线。
step "4/4 检查 scripts/*.sh 行尾符（必须 LF）"
crlf_found=0
for f in "$REPO_ROOT"/scripts/*.sh "$REPO_ROOT"/scripts/dev/*; do
  [ -f "$f" ] || continue
  case "$f" in *.md) continue ;; esac
  if grep -qU $'\r' "$f" 2>/dev/null; then
    bad "CRLF: ${f#"$REPO_ROOT"/}"
    crlf_found=1
  fi
done
[ "$crlf_found" -eq 0 ] && ok "全部为 LF"

# ── 汇总 ─────────────────────────────────────────────────────────────
printf '\n'
if [ ${#FAILED[@]} -gt 0 ]; then
  printf '\033[31m❌ lint 未通过：%s\033[0m\n' "${FAILED[*]}"
  printf '   修复 CRLF：在 Git Bash 里执行  sed -i "s/\\r$//" <文件>\n'
  exit 1
fi
printf '\033[32m✅ lint 通过\033[0m\n'
