#!/usr/bin/env bash
set -euo pipefail

mkdir -p /workspace/challenge /tmp/agan_logs /tmp/agan_cmds
chmod 777 /tmp/agan_logs /tmp/agan_cmds

# Clean exit handler so `docker stop` terminates immediately
trap 'tmux kill-server 2>/dev/null || true; exit 0' TERM INT

# Start persistent default tmux session in /workspace
if ! tmux has-session -t ctf_main 2>/dev/null; then
    tmux new-session -d -s ctf_main -c /workspace "bash --norc"
    tmux set-option -g history-limit 50000
fi

# Keep container alive while awaiting sandbox exec calls
while true; do
    sleep 3600 &
    wait $!
done
