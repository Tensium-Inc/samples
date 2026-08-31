#!/usr/bin/env bash
set -euo pipefail

SOCKET_PATH="${TASK_SOCKET:-/run/post_pci_retrieval.sock}"
STATE_PATH="${TASK_STATE_DIR:-/var/lib/post_pci_retrieval}"
WORKSPACE_PATH="${WORKSPACE_DIR:-/workspace/target}"

if [ "$(id -u)" -ne 0 ]; then
  echo "task service must start as root before the agent uid is entered" >&2
  exit 70
fi

install -d -m 0700 -o root -g root "$STATE_PATH"
rm -f "$SOCKET_PATH"
python3 /opt/task_service/daemon.py \
  --socket "$SOCKET_PATH" --state-dir "$STATE_PATH" --workspace "$WORKSPACE_PATH" \
  --candidate-uid 1000 --candidate-gid 1000 &
DAEMON_PID=$!

for _ in $(seq 1 600); do
  [ -S "$SOCKET_PATH" ] && break
  kill -0 "$DAEMON_PID" 2>/dev/null || { echo "task service exited during startup" >&2; exit 71; }
  sleep 0.10
done
[ -S "$SOCKET_PATH" ] || { echo "task service socket was not created" >&2; exit 72; }

exec "$@"
