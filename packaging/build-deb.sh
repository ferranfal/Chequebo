#!/usr/bin/env bash
# Build on Ubuntu 24.04 amd64. Nothing is installed into the host system.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python_bin="${CHEQUEBO_BUILD_PYTHON:-python3}"
version="$($python_bin -c 'from chequebo import __version__; print(__version__)')"
arch="$(dpkg --print-architecture)"
if [ "$arch" != amd64 ]; then
  printf 'This package recipe currently supports amd64 only.\n' >&2
  exit 1
fi
build_dir="$PWD/build/deb"
export PYINSTALLER_CONFIG_DIR="$build_dir/cache"
stage="$build_dir/stage"
# Only remove generated staging files, never source or user recordings.
rm -rf -- "$stage"
mkdir -p "$stage/DEBIAN" "$stage/opt" "$stage/usr/bin" \
  "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" \
  "$stage/usr/share/doc/chequebo" dist
"$python_bin" -m PyInstaller --noconfirm --clean --onedir --name chequebo \
  --paths "$PWD" --distpath "$build_dir/bundle" --workpath "$build_dir/pyinstaller" \
  --specpath "$build_dir" --hidden-import pynvml \
  --add-data "$PWD/chequebo/resources:chequebo/resources" \
  --exclude-module tkinter --exclude-module matplotlib --exclude-module scipy \
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick \
  packaging/entry.py
cp -a "$build_dir/bundle/chequebo" "$stage/opt/chequebo"
ln -s /opt/chequebo/chequebo "$stage/usr/bin/chequebo"
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
Depends: libc6 (>= 2.39), libstdc++6, python3, libgl1, libegl1, libopengl0, libxcb-cursor0, libxkbcommon-x11-0, libfontconfig1
Description: Linux desktop system monitor with live history and recording
 Monitor the whole computer, optionally save sessions, inspect time ranges and
 export peak reports. Includes an English Qt desktop interface.
EOF
chmod 755 "$stage/DEBIAN"
dpkg-deb --root-owner-group -Zxz -z3 --build "$stage" "dist/chequebo_${version}_${arch}.deb"
printf '\nCreated dist/chequebo_%s_%s.deb\n' "$version" "$arch"
