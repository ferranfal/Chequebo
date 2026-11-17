"""Recording destination shared by the desktop and command-line recorder."""

import os
from pathlib import Path

from PySide6.QtCore import QSettings


def recording_settings():
    return QSettings("Chequebo", "Chequebo")


def recording_directory(override=None, settings=None):
    if override is not None:
        return Path(override).expanduser().absolute()
    if settings is None:
        settings = recording_settings()
    default = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "chequebo"
    return Path(settings.value("recording_folder", str(default), type=str) or default).expanduser().absolute()
