#!/bin/sh
set -eu
rm -f /run/readmission-task/ops.sock
python3 /opt/task/ops_daemon.py &
pid=$!
i=0
while [ ! -S /run/readmission-task/ops.sock ]; do
  i=$((i + 1))
  [ "$i" -lt 100 ] && kill -0 "$pid" 2>/dev/null || exit 125
  sleep 0.05
done
exec setpriv --reuid=1000 --regid=1000 --init-groups --no-new-privs "$@"
