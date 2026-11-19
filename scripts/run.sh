#!/usr/bin/env bash
# Chạy backend. Dừng bằng Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -d .venv ] && source .venv/bin/activate
exec python -m backend
