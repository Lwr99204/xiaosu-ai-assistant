#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# stop.sh — 停止 start.sh 拉起的全部进程
#
# 只读取本项目生成的 PID 文件，避免误杀机器上的其他 Python / Node 进程。
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail   # 不用 -e：端口上没进程时 kill/lsof 返回非 0 属正常

# ── 变量区 ────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RUN_DIR="$REPO_ROOT/storage/run"

log() { printf '\033[36m[stop]\033[0m %s\n' "$*"; }

log "读取本项目 PID 文件 …"
if [ -d "$RUN_DIR" ]; then
  found=0
  for pidfile in "$RUN_DIR"/*.pid; do
    [ -e "$pidfile" ] || continue
    found=1
    pid="$(tr -cd '0-9' <"$pidfile")"
    if [ -n "$pid" ]; then
      if command -v taskkill.exe >/dev/null 2>&1; then
        taskkill.exe //PID "$pid" //T //F >/dev/null 2>&1 || true
      else
        kill "$pid" 2>/dev/null || true
      fi
      log "  已停止 $(basename "$pidfile" .pid)（PID $pid）"
    fi
    rm -f "$pidfile"
  done
  [ "$found" -eq 0 ] && log "  无 PID 文件"
fi

log "✅ stop 完成"
