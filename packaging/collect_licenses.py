"""Preserve the license files and versions of bundled Python dependencies."""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys

destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
packages = ["PySide6-Essentials", "shiboken6", "numpy", "pyqtgraph", "psutil",
            "nvidia-ml-py", "colorama", "pyinstaller"]
inventory = []
for name in packages:
    distribution = metadata.distribution(name)
    inventory.append(f"{name}=={distribution.version}")
    package_dir = destination / name
    package_dir.mkdir(exist_ok=True)
    (package_dir / "METADATA.txt").write_text(distribution.read_text("METADATA") or "")
    for file in distribution.files or []:
        if any(part.lower().startswith(("license", "licence", "copying", "copyright"))
               for part in file.parts):
            source = distribution.locate_file(file)
            if source.is_file():
                target = destination / name / Path(*[p for p in file.parts if p != ".."])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
# Some wheels keep their notice in module headers instead of a license file.
nvml = metadata.distribution("nvidia-ml-py").locate_file("pynvml.py").read_text()
(destination / "nvidia-ml-py/NOTICE.txt").write_text(nvml.split("\n##\n", 1)[0] + "\n")
for name in ("LGPL-3", "GPL-3"):
    shutil.copy2(Path("/usr/share/common-licenses") / name, destination / name)
(destination / "QT-NOTICE.txt").write_text(
    "Qt, PySide6 and Shiboken: Copyright The Qt Company Ltd. and contributors.\n"
    "Distributed under LGPL-3.0; the LGPL-3 and GPL-3 texts are included.\n"
    "Unmodified shared libraries are located under /opt/chequebo/_internal.\n"
    "Source code and build instructions for each version in VERSIONS.txt:\n"
    "https://download.qt.io/official_releases/QtForPython/\n"
    "https://download.qt.io/official_releases/qt/\n"
    "https://code.qt.io/cgit/pyside/pyside-setup.git/\n"
    "https://code.qt.io/cgit/qt/\n"
)
# Include the interpreter license supplied by Python itself.
python_license = __import__("builtins").license
python_license._Printer__setup()
(destination / "PYTHON-LICENSE.txt").write_text("\n".join(python_license._Printer__lines))
(destination / "VERSIONS.txt").write_text("\n".join(inventory) + "\n")
