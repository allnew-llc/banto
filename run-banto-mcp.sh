#!/bin/bash
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
# Keep the same runtime as the broker; do not resolve/update Python per client.
exec "$SCRIPT_DIR/.venv/bin/python" -m banto.mcp_server "$@"
