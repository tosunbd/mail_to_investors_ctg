#!/usr/bin/env bash
# Linux launcher.
#   ./run.sh portfolio            send Portfolio Statements
#   ./run.sh sale-purchase        send Sale/Purchase Confirmations
#   ./run.sh check                test DB + mail server
#   ./run.sh portfolio --dry-run  (any option of the Windows version works here too)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# Oracle Instant Client 11.2 for Linux x64 (see SETUP_AND_SCHEDULER_GUIDE.txt).
CLIENT_DIR="${ORACLE_CLIENT_DIR:-$PWD/oracle_client/instantclient_11_2_linux}"
if [ ! -f "$CLIENT_DIR/libclntsh.so.11.1" ] && [ ! -f "$CLIENT_DIR/libclntsh.so" ]; then
    echo "Oracle Instant Client not found in $CLIENT_DIR" >&2
    exit 2
fi
export LD_LIBRARY_PATH="$CLIENT_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if [ ! -x .venv/bin/python ]; then
    echo "Not installed yet - run ./setup_linux.sh first." >&2
    exit 2
fi
exec .venv/bin/python -m icb_mailer "$@"
