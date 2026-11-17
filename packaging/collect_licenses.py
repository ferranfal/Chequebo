"""Preserve notices for the small libraries distributed inside the package."""
from importlib import metadata
from pathlib import Path
import shutil
import sys

destination=Path(sys.argv[1])
destination.mkdir(parents=True,exist_ok=True)
distribution=metadata.distribution('nvidia-ml-py')
package_dir=destination/'nvidia-ml-py'
package_dir.mkdir(exist_ok=True)
(package_dir/'METADATA.txt').write_text(distribution.read_text('METADATA') or '')
for file in distribution.files or []:
    if any(part.lower().startswith(('license','licence','copying','copyright')) for part in file.parts):
        source=distribution.locate_file(file)
        if source.is_file():
            target=package_dir/Path(*[p for p in file.parts if p!='..'])
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
nvml=distribution.locate_file('pynvml.py').read_text()
(package_dir/'NOTICE.txt').write_text(nvml.split('\n##\n',1)[0]+'\n')
shutil.copy2('chequebo/web/vendor/LICENSE-plotly.txt',destination/'LICENSE-plotly.txt')
(destination/'VERSIONS.txt').write_text(f'Plotly.js Cartesian 3.1.0\nnvidia-ml-py {distribution.version}\n')
(destination/'SYSTEM-DEPENDENCIES.txt').write_text(
    'Python, psutil, PyQt6 and Qt WebEngine are provided by Ubuntu packages.\n'
    'Their copyright files and license terms are in /usr/share/doc/<package>/copyright.\n'
    'PyQt6 is GPL-3.0; Chequebo MIT-licensed source is GPL-compatible.\n'
)
