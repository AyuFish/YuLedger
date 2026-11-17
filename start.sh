#!/bin/sh
# start.sh - Ledger dashboard launcher for Debian / ARM Linux (POSIX sh)
#
# Usage:
#   ./start.sh                     run in foreground (Ctrl+C to stop)
#   ./start.sh daemon              run in background, log to ~/.local/state/ledger/server.log
#   ./start.sh stop                stop the background instance
#   ./start.sh restart             restart the background instance
#   ./start.sh status              show whether it is running
#   ./start.sh help                show this help
#   ./start.sh --port 9000         any other args go to ledger_server.py
#
# Env:
#   LEDGER_PORT=9000               port used by status / probe
#   LEDGER_BROWSER=1               allow auto-opening a browser on desktop
#
# First time on Linux:
#   chmod +x start.sh
#   sudo apt install python3       (if python3 is missing)
#   sudo ufw allow 8765/tcp        (only when a firewall is enabled)

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd) || exit 1
cd "$SCRIPT_DIR" || exit 1

APP=ledger_server.py
PID_FILE="$SCRIPT_DIR/ledger-server.pid"
# 日志不落在项目目录里：放到用户家目录，避免 .log 混进仓库
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ledger"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="${TMPDIR:-/tmp}"
LOG_FILE="$STATE_DIR/server.log"
PY=
PID=
PORT=8765
NO_BROWSER=

# Chinese banner + unbuffered logs, even when the box runs with LANG=C
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

show_help() {
  cat <<'EOF'
Ledger dashboard launcher (Debian / ARM Linux, POSIX sh)

  ./start.sh                  foreground, Ctrl+C stops it
  ./start.sh daemon           background, writes ~/.local/state/ledger/server.log
  ./start.sh stop             stop background instance
  ./start.sh restart          restart background instance
  ./start.sh status           show PID and port state
  ./start.sh --port 9000      pass extra args to ledger_server.py
  ./start.sh --host 127.0.0.1 localhost only (default is 0.0.0.0)

Access log (foreground): fixed 10-line area, rewritten in place with ANSI
cursor moves, showing time / client IP / device (Android, iPhone, PC, Linux).
Daemon mode redirects stdout to server.log, so that file keeps every line
(line-by-line instead of in-place refresh). Ctrl+C stops foreground mode.

If copied from Windows and you see "bad interpreter: ^M":
  sed -i 's/\r$//' start.sh && chmod +x start.sh

For always-on boards, use systemd with:
  ExecStart=/usr/bin/python3 /path/to/ledger_server.py --no-browser
  WorkingDirectory=/path/to/folder
EOF
}

out() { printf '%s\n' "$*"; }

find_python() {
  for c in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1; then
        PY=$c
        return 0
      fi
    fi
  done
  return 1
}

preflight() {
  if [ ! -f "$APP" ]; then
    out "ERROR: $APP not found in $SCRIPT_DIR"
    return 1
  fi
  if [ ! -f index.html ]; then
    out "WARNING: index.html not found in $SCRIPT_DIR"
  fi
  if [ ! -w "$SCRIPT_DIR" ]; then
    out "ERROR: $SCRIPT_DIR is not writable (ledger.db and logs live here)"
    return 1
  fi
  return 0
}

port_from_args() {
  PORT=${LEDGER_PORT:-8765}
  prev=
  for a in "$@"; do
    case "$prev" in --port) PORT=$a ;; esac
    case "$a" in --port=*) PORT=${a#--port=} ;; esac
    prev=$a
  done
}

port_in_use() {
  "$PY" -c 'import socket, sys
s = socket.socket()
s.settimeout(0.4)
r = s.connect_ex(("127.0.0.1", int(sys.argv[1])))
s.close()
raise SystemExit(0 if r == 0 else 1)' "$PORT" >/dev/null 2>&1
}

browser_flag() {
  if [ "${LEDGER_BROWSER:-0}" = "1" ] || [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    NO_BROWSER=
  else
    NO_BROWSER=--no-browser
  fi
}

read_pid() {
  [ -f "$PID_FILE" ] || return 1
  PID=$(cat "$PID_FILE" 2>/dev/null) || return 1
  case "$PID" in ''|*[!0-9]*) return 1 ;; esac
  return 0
}

is_running() {
  read_pid || return 1
  kill -0 "$PID" 2>/dev/null || return 1
  return 0
}

rotate_log() {
  LOG_SIZE=$("$PY" -c 'import os, sys
print(os.path.getsize(sys.argv[1]) if os.path.exists(sys.argv[1]) else 0)' "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$LOG_SIZE" -gt 1048576 ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || :
  fi
}

start_foreground() {
  if port_in_use; then
    out "Port $PORT is already in use. Stop the other instance first,"
    out "or use: ./start.sh --port 9000"
    return 1
  fi
  out "Ledger dashboard - SQLite local service"
  out "Dir:    $SCRIPT_DIR"
  out "Python: $PY ($("$PY" -V 2>&1))"
  out "LAN URLs below; access log: time / client IP / device (Android, iPhone, PC, Linux), fixed area. Ctrl+C to stop."
  out ""
  if [ -n "$NO_BROWSER" ]; then
    exec "$PY" -u "$APP" "$NO_BROWSER" "$@"
  else
    exec "$PY" -u "$APP" "$@"
  fi
}

start_daemon() {
  if is_running; then
    out "Already running (PID $PID), port $PORT."
    return 0
  fi
  rm -f "$PID_FILE"
  rotate_log
  if port_in_use; then
    out "Port $PORT is already in use by another process."
    out "Use './start.sh stop' for a daemon started by this script,"
    out "or pick another port: ./start.sh daemon --port 9000"
    return 1
  fi
  if [ -n "$NO_BROWSER" ]; then
    nohup "$PY" -u "$APP" "$NO_BROWSER" "$@" >> "$LOG_FILE" 2>&1 &
  else
    nohup "$PY" -u "$APP" "$@" >> "$LOG_FILE" 2>&1 &
  fi
  CHILD=$!
  echo "$CHILD" > "$PID_FILE"
  sleep 2
  if ! kill -0 "$CHILD" 2>/dev/null; then
    out "Failed to start. Last lines of $LOG_FILE:"
    tail -n 15 "$LOG_FILE" 2>/dev/null || :
    rm -f "$PID_FILE"
    return 1
  fi
  out "Started in background (PID $CHILD)."
  if port_in_use; then
    out "Port $PORT is listening."
  else
    out "Port probe from 127.0.0.1 failed - custom --host? Check the log."
  fi
  out "Log: $LOG_FILE"
  return 0
}

stop_daemon() {
  if ! read_pid; then
    out "No PID file - nothing to stop."
    rm -f "$PID_FILE" 2>/dev/null || :
    return 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    out "Process $PID is not running; removed stale PID file."
    rm -f "$PID_FILE"
    return 0
  fi
  printf 'Stopping PID %s ... ' "$PID"
  kill -INT "$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || :
  i=0
  while [ "$i" -lt 5 ]; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID" 2>/dev/null || :
    sleep 1
  fi
  if kill -0 "$PID" 2>/dev/null; then
    kill -KILL "$PID" 2>/dev/null || :
  fi
  rm -f "$PID_FILE"
  out "stopped."
  return 0
}

status_daemon() {
  if is_running; then
    out "Running (PID $PID)."
  else
    out "No daemon running."
    if [ -f "$PID_FILE" ]; then
      out "Stale PID file: $PID_FILE"
    fi
  fi
  if port_in_use; then
    out "Port $PORT is listening."
  else
    out "Port $PORT is not reachable."
  fi
  return 0
}

case "${1:-}" in
  help|-h|--help)
    show_help
    exit 0
    ;;
esac

find_python || {
  out "ERROR: Python 3.7+ not found."
  out "On Debian / Armbian: sudo apt update && sudo apt install python3"
  exit 1
}
preflight || exit 1
browser_flag

CMD=${1:-}
case "$CMD" in
  daemon|start-bg|-d)
    shift
    port_from_args "$@"
    start_daemon "$@"
    ;;
  stop)
    shift
    port_from_args "$@"
    stop_daemon
    ;;
  restart)
    shift
    port_from_args "$@"
    stop_daemon
    start_daemon "$@"
    ;;
  status)
    shift
    port_from_args "$@"
    status_daemon
    ;;
  *)
    port_from_args "$@"
    start_foreground "$@"
    ;;
esac
