#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# check_limits.sh — 工程规模门禁（FR-6-17 / TDD §3 / ADR-14）
#
# 🔴 M0 即完整实现（其余 7 个脚本此阶段只出骨架）：本脚本纯文件遍历、
#    零外部依赖，可立即当门禁用，并纳入 pre-commit / pre-push 钩子。
#
# 检查两条底线：
#   1) 单个代码文件        ≤ 500 行
#   2) 单个目录的【直接】代码文件数 ≤ 8 个
#
# 统计口径（与 TDD §3 目录约定、README「工程规范」小节严格一致）：
#   ① 只数目录的**直接子文件**，子目录不计入
#   ② 只统计**代码文件** .py/.ts/.tsx/.js/.jsx；截图/录屏/.md 语料/
#      .pdf/.docx 等资源与文档**不计入**（否则 docs/screenshots/ 必误报）
#   ③ __init__.py 与 .gitkeep **不计入目录文件数**，但 __init__.py
#      **仍受 500 行约束**（TDD §3 约定第 4 条）
#   ④ 第三方与生成物目录整体排除（.venv / node_modules / .next / …）
#
# 环境变量可覆盖阈值：MAX_LINES=400 MAX_FILES=6 bash scripts/check_limits.sh
# ═══════════════════════════════════════════════════════════════════════

# 故意不用 -e：grep/find 无匹配时返回非 0，需自行汇总判定
set -uo pipefail

MAX_LINES="${MAX_LINES:-500}"
MAX_FILES="${MAX_FILES:-8}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# ── 排除目录：第三方依赖与构建产物，不属于本项目代码 ──────────────
prune=( -name .git )
for d in .venv venv env node_modules .next out dist build \
         __pycache__ .pytest_cache .ruff_cache .uv-cache .mypy_cache \
         coverage htmlcov .turbo storage; do
  prune+=( -o -name "$d" )
done

code_files="$(find . \( "${prune[@]}" \) -prune -o -type f \
  \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \
     -o -name '*.js' -o -name '*.jsx' \) -print \
  | sed 's|^\./||' | sort)"

if [ -z "$code_files" ]; then
  echo "✅ 尚未发现代码文件（M0 骨架阶段），无超限"
  exit 0
fi

# ── 检查 1：单文件行数 ────────────────────────────────────────────
# 用 awk END{NR} 而非 wc -l：文件末行无换行符时 wc -l 会少算 1 行
line_violations=""
scanned=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  scanned=$((scanned + 1))
  n="$(awk 'END{print NR}' "$f" 2>/dev/null || echo 0)"
  if [ "$n" -gt "$MAX_LINES" ]; then
    line_violations="${line_violations}  ✗ ${f} → ${n} 行（上限 ${MAX_LINES}）"$'\n'
  fi
done <<< "$code_files"

# ── 检查 2：目录直接代码文件数（剔除 __init__.py，见口径 ③）──────
countable="$(printf '%s\n' "$code_files" | grep -Ev '(^|/)__init__\.py$' || true)"

dir_violations=""
if [ -n "$countable" ]; then
  dir_stats="$(printf '%s\n' "$countable" | while IFS= read -r f; do
      [ -z "$f" ] && continue
      d="${f%/*}"
      [ "$d" = "$f" ] && d="."   # 位于仓库根目录的文件
      printf '%s\n' "$d"
    done | sort | uniq -c)"

  while read -r cnt dir; do
    [ -z "${cnt:-}" ] && continue
    if [ "$cnt" -gt "$MAX_FILES" ]; then
      dir_violations="${dir_violations}  ✗ ${dir}/ → ${cnt} 个代码文件（上限 ${MAX_FILES}）"$'\n'
    fi
  done <<< "$dir_stats"
fi

# ── 汇总 ─────────────────────────────────────────────────────────
if [ -n "$line_violations" ] || [ -n "$dir_violations" ]; then
  echo "❌ 工程规模门禁未通过（扫描 ${scanned} 个代码文件）"
  if [ -n "$line_violations" ]; then
    echo ""
    echo "【超过 ${MAX_LINES} 行的文件】"
    printf '%s' "$line_violations"
  fi
  if [ -n "$dir_violations" ]; then
    echo ""
    echo "【超过 ${MAX_FILES} 个代码文件的目录】"
    printf '%s' "$dir_violations"
  fi
  echo ""
  echo "→ 处理方式：拆分文件；或在该目录下新建子目录分层。"
  echo "  ⚠️ scripts/ 已达 8 上限；其余目录按上方清单核对剩余额度，"
  echo "     需要新增文件时先建子目录分层（TDD R12 / §3 目录约定第 5 条）。"
  exit 1
fi

echo "✅ 所有文件 ≤${MAX_LINES} 行，所有目录 ≤${MAX_FILES} 文件（扫描 ${scanned} 个代码文件）"
