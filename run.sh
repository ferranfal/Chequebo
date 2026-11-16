#!/usr/bin/env bash
set -euo pipefail
chequebo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -x "$chequebo_dir/.venv/bin/python" ]; then
  printf 'Primero ejecuta ./install.sh desde la carpeta de Chequebo.\n' >&2
  exit 1
fi
export PYTHONPATH="$chequebo_dir${PYTHONPATH:+:$PYTHONPATH}"
for library_dir in "$chequebo_dir"/.venv/native/usr/lib/*-linux-gnu; do
  if [ -d "$library_dir" ]; then
    export LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
done
exec "$chequebo_dir/.venv/bin/python" -m chequebo "$@"
