#!/usr/bin/env bash
set -euo pipefail

BASTION="cc@129.114.25.255"
BKEY="${HOME}/.ssh/F25_BASTION.pem"   # <<<< change for your Mac

MAP=(
  "2201:192.168.5.21:22"  # team2_vm1
  "2202:192.168.5.70:22"  # team2_vm2
  "2203:192.168.5.95:22"  # team2_vm3
  "2204:192.168.5.128:22" # team2_vm4
  "2205:192.168.5.94:22"  # team2_vm5
)

busy=0
for spec in "${MAP[@]}"; do
  lport="${spec%%:*}"
  if lsof -n -iTCP:"$lport" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $lport already in use. Run ./stop_tunnels.sh first." >&2
    busy=1
  fi
done
if [[ "$busy" -ne 0 ]]; then exit 1; fi

FORWARDS=""
for spec in "${MAP[@]}"; do
  FORWARDS+=" -L ${spec}"
done

echo "Starting tunnels via $BASTION:"
for spec in "${MAP[@]}"; do echo "  localhost:${spec}"; done

exec ssh -i "$BKEY" -fN \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  $FORWARDS "$BASTION"
