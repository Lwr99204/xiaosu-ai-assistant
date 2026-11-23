#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# stop.sh — 停止 start.sh 拉起的全部进程
#
# 状态：🚧 M0 骨架；完整实现在 M10
#
# 为什么需要独立脚本：start.sh 内部用 trap 清理自己的子进程，但若终端被
# 强杀（关窗口 / kill -9），trap 不会执行，残留的 uvicorn 与 next dev 会
# 继续占着 8000 / 3000 端口，导致下次启动报 "address already in use"。
# 本脚本按端口兜底清理。
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail   # 不用 -e：端口上没进程时 kill/lsof 返回非 0 属正常

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORTS=("${BACKEND_PORT:-8000}" "${FRONTEND_PORT:-3000}")

log() { printf '\033[36m[stop]\033[0m %s\n' "$*"; }

# ── 步骤 1/2：按端口找 PID ────────────────────────────────────────────
log "1/2 扫描端口 ${PORTS[*]} …"
# M10 实现要点：
#   · 优先用 lsof -ti tcp:<port>；Git Bash 无 lsof 时回退 netstat -ano
#   · Windows/Git Bash 下 netstat 输出的是 Windows PID，需用 taskkill //PID //F
#     （注意 // 双斜杠，避免 MSYS 把 /PID 当路径转换 —— 这是实测常踩的坑）
#   · 只杀本项目端口上的进程，不要用 pkill -f python 这类宽泛匹配，
#     否则会误杀用户其他 Python 进程（含 embedding 服务）
for port in "${PORTS[@]}"; do
  log "  :$port —— 待 M10 实现（当前为占位）"
done

# ── 步骤 2/2：PID 文件兜底 ────────────────────────────────────────────
log "2/2 读取 PID 文件 …"
# M10 实现要点：start.sh 启动时把子进程 PID 写入 storage/*.pid，
#              此处逐个读取并 kill，比按端口扫描更精确
if [ -d "$REPO_ROOT/storage" ]; then
  found=0
  for pidfile in "$REPO_ROOT"/storage/*.pid; do
    [ -e "$pidfile" ] || continue
    found=1
    log "  发现 $(basename "$pidfile") —— 待 M10 实现"
  done
  [ "$found" -eq 0 ] && log "  无 PID 文件"
fi

log "✅ stop 完成（M0 阶段为骨架，未实际终止进程）"
log "   ⚠ embedding 服务（:8001）是独立进程，需自行 Ctrl+C 停止"
