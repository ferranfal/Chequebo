#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if command -v uv >/dev/null 2>&1; then
  if [ ! -x .venv/bin/python ]; then uv venv .venv; fi
  uv pip install --python .venv/bin/python -e '.[nvidia]'
else
  if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
  .venv/bin/python -m ensurepip --upgrade
  .venv/bin/python -m pip install -e '.[nvidia]'
fi
printf 'Chequebo instalado. Abre la aplicación con ./run.sh\n'
