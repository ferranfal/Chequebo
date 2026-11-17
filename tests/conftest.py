import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication, QEvent


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication(["chequebo-tests"])
    yield application
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
