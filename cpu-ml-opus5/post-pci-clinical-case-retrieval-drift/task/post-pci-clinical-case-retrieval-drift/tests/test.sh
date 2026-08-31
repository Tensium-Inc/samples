#!/usr/bin/env bash
set -euo pipefail
if find /tests -xdev -type l -print -quit | grep -q .; then
  echo "verifier tree contains a symlink" >&2
  exit 74
fi
chown -R root:root /tests
find /tests -xdev -type d -exec chmod 0700 {} +
find /tests -xdev -type f -exec chmod 0600 {} +
exec python3 /tests/grade.py
