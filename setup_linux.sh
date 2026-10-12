#!/usr/bin/env bash
# One-time setup on Linux (safe to re-run). Needs Python 3.12 (64-bit) and
# Oracle Instant Client 11.2 Linux x64 unzipped into
# oracle_client/instantclient_11_2_linux - see SETUP_AND_SCHEDULER_GUIDE.txt.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null || { echo "$PY not found - install Python 3.12 first." >&2; exit 2; }

CLIENT_DIR="${ORACLE_CLIENT_DIR:-$PWD/oracle_client/instantclient_11_2_linux}"
if [ -f "$CLIENT_DIR/libclntsh.so.11.1" ] && [ ! -e "$CLIENT_DIR/libclntsh.so" ]; then
    ln -s libclntsh.so.11.1 "$CLIENT_DIR/libclntsh.so"
fi

[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
chmod +x run.sh

./run.sh check
