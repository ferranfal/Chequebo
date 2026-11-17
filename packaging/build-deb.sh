#!/usr/bin/env bash
# Build a small Ubuntu package; APT supplies Python and Qt WebEngine.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python_bin="${CHEQUEBO_BUILD_PYTHON:-python3}"
version="$($python_bin -c 'from chequebo import __version__; print(__version__)')"
arch="$(dpkg --print-architecture)"
build_dir="$PWD/build/deb"
stage="$build_dir/stage"
rm -rf -- "$stage"
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/lib/chequebo" \
  "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" \
  "$stage/usr/share/doc/chequebo" dist
"$python_bin" - "$stage/usr/lib/chequebo" <<'PY'
import importlib.util, shutil, sys
from pathlib import Path
root=Path(sys.argv[1])
shutil.copytree('chequebo', root/'chequebo', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
# Include the small official NVIDIA Python binding, without requiring an NVIDIA
# driver on computers that use AMD, Intel or no supported GPU.
shutil.copy2(importlib.util.find_spec('pynvml').origin, root/'pynvml.py')
PY
install -m 755 packaging/entry.py "$stage/usr/bin/chequebo"
install -m 644 packaging/io.github.ferranfal.Chequebo.desktop "$stage/usr/share/applications/"
install -m 644 chequebo/resources/chequebo.svg "$stage/usr/share/icons/hicolor/scalable/apps/io.github.ferranfal.Chequebo.svg"
install -m 644 LICENSE "$stage/usr/share/doc/chequebo/copyright"
install -m 644 README.md "$stage/usr/share/doc/chequebo/README.md"
"$python_bin" packaging/collect_licenses.py "$stage/usr/share/doc/chequebo/third-party"
installed_size="$(du -sk "$stage" | cut -f1)"
cat > "$stage/DEBIAN/control" <<EOF
Package: chequebo
Version: $version
Section: utils
Priority: optional
Architecture: $arch
Maintainer: Chequebo contributors <89909261+ferranfal@users.noreply.github.com>
Homepage: https://github.com/ferranfal/Chequebo
Installed-Size: $installed_size
Depends: python3 (>= 3.10), python3-psutil (>= 5.9), python3-pyqt6, python3-pyqt6.qtwebengine, python3-pyqt6.qtwebchannel
Description: Linux desktop system monitor with live Plotly charts and recording
 Monitor CPU, RAM, swap, GPU, disk and network. Save sessions, inspect time
 ranges and export peaks. Includes offline Plotly charts and a desktop icon.
EOF
chmod 755 "$stage/DEBIAN"
dpkg-deb --root-owner-group -Zxz -z3 --build "$stage" "dist/chequebo_${version}_${arch}.deb"
printf '\nCreated dist/chequebo_%s_%s.deb\n' "$version" "$arch"
