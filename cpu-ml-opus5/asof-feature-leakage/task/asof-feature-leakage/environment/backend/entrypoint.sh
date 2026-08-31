#!/bin/sh
set -eu

rm -f /run/asof-task/ops.sock
/usr/local/bin/python3 -I /opt/task/ops_daemon.py &
broker_pid=$!

attempt=0
while [ ! -S /run/asof-task/ops.sock ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 100 ] || ! kill -0 "$broker_pid" 2>/dev/null; then
        echo "operation broker failed to start" >&2
        exit 125
    fi
    sleep 0.05
done

exec "$@"
