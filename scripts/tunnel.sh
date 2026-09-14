#!/usr/bin/env bash
# assistant 远程隧道按需启停脚本。
#
# 用法：
#   ./tunnel.sh start    启动隧道（已在运行则跳过）
#   ./tunnel.sh stop     停止隧道
#   ./tunnel.sh restart  重启隧道
#   ./tunnel.sh status   查看状态（含健康探测）
#
# 隧道：本地 18081 -> 远程服务器 127.0.0.1:18080（assistant API）。
# 心跳每 10 秒一次，防止远程 sshd 掐断空闲连接；断线时进程会自行退出，
# 由调用方（或人工）再次 start。

set -euo pipefail

LOCAL_PORT="${ASSISTANT_TUNNEL_LOCAL_PORT:-18081}"
REMOTE_HOST="${ASSISTANT_TUNNEL_REMOTE_HOST:-ubuntu@43.139.236.58}"
REMOTE_PORT="${ASSISTANT_TUNNEL_REMOTE_PORT:-22}"
REMOTE_TARGET_PORT="${ASSISTANT_TUNNEL_TARGET_PORT:-18080}"
PID_FILE="${TMPDIR:-/tmp}/assistant-tunnel-${LOCAL_PORT}.pid"
HEALTH_URL="http://127.0.0.1:${LOCAL_PORT}/health"

log() { printf '[tunnel] %s\n' "$*"; }

alive_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      printf '%s' "$pid"
      return 0
    fi
  fi
  return 1
}

start_tunnel() {
  local pid
  if pid="$(alive_pid)"; then
    log "已在运行 (pid ${pid})，跳过启动。"
    return 0
  fi
  # 清掉可能残留的监听端口占用者（非本脚本管理的旧进程）。
  if lsof -ti ":${LOCAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    log "端口 ${LOCAL_PORT} 已被其他进程占用，不自动清理。请先手动处理：lsof -i :${LOCAL_PORT}"
    return 1
  fi
  log "启动隧道：本地 ${LOCAL_PORT} -> ${REMOTE_HOST}:${REMOTE_TARGET_PORT}"
  nohup ssh -N \
    -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_TARGET_PORT}" \
    -p "${REMOTE_PORT}" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=10 \
    -o ServerAliveCountMax=6 \
    -o TCPKeepAlive=yes \
    "$REMOTE_HOST" >/dev/null 2>&1 &
  local ssh_pid=$!
  echo "$ssh_pid" > "$PID_FILE"
  disown "$ssh_pid" 2>/dev/null || true
  # 等待转发端口就绪，最多 10 秒。
  for _ in $(seq 1 20); do
    if curl -sf -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
      log "隧道已就绪 (pid ${ssh_pid})：${HEALTH_URL}"
      return 0
    fi
    if ! kill -0 "$ssh_pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      log "SSH 进程启动失败，请检查网络与密钥。"
      return 1
    fi
    sleep 0.5
  done
  log "SSH 已启动 (pid ${ssh_pid})，但健康检查暂未通过；远端服务可能未就绪。"
  return 0
}

stop_tunnel() {
  local pid
  if pid="$(alive_pid)"; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.3
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    log "已停止 (pid ${pid})。"
    return 0
  fi
  if lsof -ti ":${LOCAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    log "没有本脚本管理的进程，但端口 ${LOCAL_PORT} 仍被占用；如需释放请手动处理：lsof -i :${LOCAL_PORT}"
    return 1
  fi
  log "隧道未在运行。"
  return 0
}

status_tunnel() {
  local pid
  if pid="$(alive_pid)"; then
    if curl -sf -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
      log "运行中 (pid ${pid})，远端健康检查通过。"
    else
      log "进程存在 (pid ${pid})，但远端健康检查失败——连接可能已僵死，建议 restart。"
    fi
    return 0
  fi
  log "未运行。"
  return 1
}

case "${1:-status}" in
  start) start_tunnel ;;
  stop) stop_tunnel ;;
  restart) stop_tunnel || true; start_tunnel ;;
  status) status_tunnel ;;
  *)
    printf '用法: %s {start|stop|restart|status}\n' "$0"
    exit 2
    ;;
esac
