#!/usr/bin/env bash
set -euo pipefail
chequebo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -x "$chequebo_dir/.venv/bin/python" ]; then
  printf 'Primero ejecuta ./install.sh desde la carpeta de Chequebo.\n' >&2
  exit 1
fi
export PYTHONPATH="$chequebo_dir${PYTHONPATH:+:$PYTHONPATH}"
exec "$chequebo_dir/.venv/bin/python" -m chequebo "$@"

