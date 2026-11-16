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
# Qt 6 necesita esta biblioteca en X11. En Debian/Ubuntu podemos obtenerla
# del repositorio de paquetes y usarla solo dentro de .venv, sin sudo.
qt_plugin="$(.venv/bin/python -c 'from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath) + "/platforms/libqxcb.so")')"
if command -v ldd >/dev/null 2>&1 && ldd "$qt_plugin" 2>/dev/null | grep -q 'libxcb-cursor.so.0 => not found'; then
  if command -v apt-get >/dev/null 2>&1 && command -v dpkg-deb >/dev/null 2>&1; then
    download_dir="$(mktemp -d .venv/native-download.XXXXXX)"
    mkdir -p .venv/native
    (
      cd "$download_dir"
      apt-get download libxcb-cursor0
      for package in libxcb-cursor0_*.deb; do dpkg-deb -x "$package" ../native; done
    )
  else
    printf 'Qt necesita libxcb-cursor para X11. Instálala con el gestor de paquetes de tu distribución.\n' >&2
  fi
fi
printf 'Chequebo instalado. Abre la aplicación con ./run.sh\n'
