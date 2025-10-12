#!/usr/bin/env bash
set -euo pipefail

PORTS=(2201 2202 2203 2204 2205)

for p in "${PORTS[@]}"; do
  # Find any process listening on the port and kill it
  if lsof -t -n -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    pid=$(lsof -t -n -iTCP:"$p" -sTCP:LISTEN | head -n1)
    echo "Killing PID $pid for port $p"
    kill "$pid" || true
  fi
done
