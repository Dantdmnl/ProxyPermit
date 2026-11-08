#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is missing. Install python3 with apt, then run the installer again." >&2
    exit 1
fi
exec python3 "${SCRIPT_DIR}/scripts/deploy/install.py" "$@"
