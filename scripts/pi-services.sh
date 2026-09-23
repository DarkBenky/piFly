#!/usr/bin/env bash
set -euo pipefail

REPO="/home/user/Desktop/piFly"
RUN_DIR="/home/user"
PY="/usr/bin/python"

TEMPERATURE_SESSION="temperature-service"
TEMPERATURE_PATTERN="services/temperature/client.py"
TEMPERATURE_CMD="exec $PY Desktop/piFly/services/temperature/client.py --no-log"

COLLECTOR_SESSION="collector-service"
COLLECTOR_PATTERN="services/collector/collector.py"
COLLECTOR_CMD="exec $PY Desktop/piFly/services/collector/collector.py --static --no-log --display"

session_exists() {
  tmux has-session -t "$1" 2>/dev/null
}

process_pid() {
  pgrep -f "$1" 2>/dev/null | head -1 || true
}

start_one() {
  local name="$1" pattern="$2" command="$3" pid
  if session_exists "$name"; then
    echo "$name: already running in tmux"
    return 0
  fi
  pid="$(process_pid "$pattern")"
  if [ -n "$pid" ]; then
    echo "$name: skipped, process already running without tmux (pid $pid)"
    return 0
  fi
  tmux new-session -d -s "$name" -c "$RUN_DIR" "$command"
  echo "$name: started"
}

stop_one() {
  local name="$1" pattern="$2" pid
  pid="$(process_pid "$pattern")"
  if [ -n "$pid" ]; then
    kill -INT "$pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    echo "$name: process $pid stopped"
  fi
  if session_exists "$name"; then
    tmux kill-session -t "$name" 2>/dev/null || true
  fi
  if [ -z "$pid" ] && ! session_exists "$name"; then
    echo "$name: not running"
  fi
}

status_one() {
  local name="$1" pattern="$2" pid
  pid="$(process_pid "$pattern")"
  if session_exists "$name"; then
    echo "$name: tmux session up (pid ${pid:-unknown})"
  elif [ -n "$pid" ]; then
    echo "$name: running outside tmux (pid $pid)"
  else
    echo "$name: down"
  fi
}

start_all() {
  start_one "$TEMPERATURE_SESSION" "$TEMPERATURE_PATTERN" "$TEMPERATURE_CMD"
  start_one "$COLLECTOR_SESSION" "$COLLECTOR_PATTERN" "$COLLECTOR_CMD"
}

stop_all() {
  stop_one "$COLLECTOR_SESSION" "$COLLECTOR_PATTERN"
  stop_one "$TEMPERATURE_SESSION" "$TEMPERATURE_PATTERN"
}

case "${1:-start}" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; sleep 1; start_all ;;
  status)
    status_one "$TEMPERATURE_SESSION" "$TEMPERATURE_PATTERN"
    status_one "$COLLECTOR_SESSION" "$COLLECTOR_PATTERN"
    ;;
  *)
    echo "usage: pi-services [start|stop|restart|status]"
    exit 1
    ;;
esac
