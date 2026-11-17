"""One offline WebEngine surface for all Plotly charts."""

import json
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QTimer, QUrl, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QFileDialog


class LocalRequests(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() not in ('file', 'qrc', 'data', 'blob'):
            info.block(True)


class ChartBridge(QObject):
    received = pyqtSignal(dict)

    @pyqtSlot(str)
    def send(self, message):
        try:
            event = json.loads(message)
            if isinstance(event, dict):
                self.received.emit(event)
        except (ValueError, TypeError):
            pass


class PlotlyDashboard(QWebEngineView):
    event_received = pyqtSignal(dict)
    ready_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ready = False
        self.payload = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # Keep the profile alive for longer than every page. In particular,
        # collecting a Python profile wrapper during window teardown is unsafe.
        app = QApplication.instance()
        if not hasattr(app, '_chequebo_profile'):
            profile = app._chequebo_profile = QWebEngineProfile(app)
            profile._chequebo_local = LocalRequests(profile)
            profile.setUrlRequestInterceptor(profile._chequebo_local)
        profile = self.profile = app._chequebo_profile
        page = QWebEnginePage(profile, self)
        self.setPage(page)
        profile.downloadRequested.connect(self.save_download)
        self.channel = QWebChannel(page)
        self.bridge = ChartBridge(self.channel)
        self.bridge.received.connect(self.receive)
        self.channel.registerObject('bridge', self.bridge)
        page.setWebChannel(self.channel)
        self.flush_timer = QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.timeout.connect(self.flush)
        self.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'web/dashboard.html')))

    def receive(self, event):
        if event.get('type') == 'ready':
            self.ready = True
            self.ready_changed.emit(True)
            self.flush()
        else:
            self.event_received.emit(event)

    def render(self, payload):
        self.payload = payload
        if self.ready and not self.flush_timer.isActive():
            self.flush_timer.start(16)

    def flush(self):
        if self.ready and self.payload is not None:
            encoded = json.dumps(self.payload, allow_nan=False, separators=(',', ':'))
            self.page().runJavaScript('window.Chequebo.render(' + encoded + ');')

    def fullscreen(self, key=None):
        self.page().runJavaScript('window.Chequebo.fullscreen(' + json.dumps(key) + ');')

    def dispose(self):
        self.ready = False
        self.flush_timer.stop()
        self.payload = None
        self.stop()
        page = self.page()
        page.setWebChannel(None)
        # The application can quit as soon as its last window closes. Destroy
        # the page now, before the application-owned Chromium profile is freed.
        page.deleteLater()
        QCoreApplication.sendPostedEvents(page, QEvent.Type.DeferredDelete)

    def save_download(self, download):
        if download.page() != self.page():
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save chart image', 'chequebo-chart.png', 'PNG image (*.png)')
        if not path:
            download.cancel()
            return
        target = Path(path)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()
