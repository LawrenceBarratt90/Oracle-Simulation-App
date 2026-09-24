#!/usr/bin/env bash
#
# Start, stop, restart, or check the status of a simulator run.
# Supports running multiple customers at once — each env file gets its own
# PID and log file, so you can run e.g. Acme and Beta Co simultaneously.
#
# Usage:
#   ./simulator_ctl.sh start   [customers/acme.env] [-- extra simulator.py args]
#   ./simulator_ctl.sh stop    [customers/acme.env]
#   ./simulator_ctl.sh restart [customers/acme.env]
#   ./simulator_ctl.sh status  [customers/acme.env]
#
# If no env file is given, uses .env in this folder.

set -euo pipefail
cd "$(dirname "$0")"

ACTION="${1:-}"
ENV_FILE="${2:-.env}"
shift $(( $# >= 2 ? 2 : $# )) || true

# Extra args after "--" are passed straight through to simulator.py
EXTRA_ARGS=()
if [[ "${1:-}" == "--" ]]; then
    shift
    EXTRA_ARGS=("$@")
fi

SLUG=$(basename "$ENV_FILE" | tr -c 'a-zA-Z0-9' '_')
PID_FILE="run/${SLUG}.pid"
LOG_FILE="run/${SLUG}.log"
mkdir -p run

start() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Already running for $ENV_FILE (PID $(cat "$PID_FILE"))."
        exit 0
    fi
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "Env file not found: $ENV_FILE"
        echo "Copy customers/example.env.example to $ENV_FILE and fill it in first."
        exit 1
    fi
    echo "Starting simulator for $ENV_FILE ..."
    nohup python simulator.py --env-file "$ENV_FILE" "${EXTRA_ARGS[@]}" \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    echo "Started (PID $(cat "$PID_FILE")). Logs: $LOG_FILE"
}

stop() {
    if [[ ! -f "$PID_FILE" ]] || ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Not running for $ENV_FILE."
        rm -f "$PID_FILE"
        exit 0
    fi
    PID=$(cat "$PID_FILE")
    echo "Stopping PID $PID ..."
    kill "$PID"
    rm -f "$PID_FILE"
    echo "Stopped."
}

status() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Running for $ENV_FILE (PID $(cat "$PID_FILE")). Tail: tail -f $LOG_FILE"
    else
        echo "Not running for $ENV_FILE."
    fi
}

case "$ACTION" in
    start)   start ;;
    stop)    stop ;;
    restart) stop || true; start ;;
    status)  status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status} [env-file] [-- extra args]"
        exit 1
        ;;
esac
