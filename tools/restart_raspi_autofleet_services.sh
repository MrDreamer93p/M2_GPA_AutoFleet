#!/usr/bin/env bash
set -euo pipefail

sudo systemctl stop autofleet-agent.service autofleet-stream.service autofleet-mediamtx.service || true
tmux kill-session -t robot 2>/dev/null || true
sleep 1

mapfile -t pids < <(
  ps -eo pid=,comm=,args= |
    awk '$2=="mediamtx" || $2=="rpicam-vid" || ($2=="python3" && ($0 ~ /stream_rtsp[.]py/ || $0 ~ /raspi_autofleet_agent[.]py/)) {print $1}'
)
if ((${#pids[@]})); then
  kill "${pids[@]}" 2>/dev/null || true
  sleep 1
fi

sudo systemctl restart autofleet-mediamtx.service autofleet-stream.service autofleet-agent.service
sleep 8
systemctl --no-pager --full status autofleet-mediamtx.service autofleet-stream.service autofleet-agent.service
