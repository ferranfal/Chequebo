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
    for file in distribution.files or []:
        if any(part.lower().startswith(("license", "licence", "copying", "copyright"))
               for part in file.parts):
            source = distribution.locate_file(file)
            if source.is_file():
                target = destination / name / Path(*[p for p in file.parts if p != ".."])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
# Include the interpreter license supplied by Python itself.
python_license = __import__("builtins").license
python_license._Printer__setup()
(destination / "PYTHON-LICENSE.txt").write_text("\n".join(python_license._Printer__lines))
(destination / "VERSIONS.txt").write_text("\n".join(inventory) + "\n")
