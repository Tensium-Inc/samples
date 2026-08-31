#!/bin/bash
# Start the serving daemon as root, then hand off to whatever this container was told to
# run (a shell for the Harbor path, `hud serve` for the HUD path).
set -e
if [ "$(id -u)" = "0" ] && [ ! -S /run/envd.sock ]; then
  /usr/local/bin/python3 /opt/env/envd.py serve >/var/log/envd.log 2>&1 &
  for _ in $(seq 1 50); do [ -S /run/envd.sock ] && break; sleep 0.1; done
fi
exec "$@"
