"""Qt window. Sampling and persistence run outside the UI thread."""

import bisect
import math
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import psutil
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDockWidget,
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .charts import PlotlyDashboard
from .metrics import GPU
from .monitor import SystemMonitor
from .inspector import ProcessInspector
from .peaks import describe_peak
from .preferences import recording_directory, recording_settings
from .session import clock, display_metric, export_maxima, export_samples, export_processes, load_session, maximum, metrics_for, metric_value


class Window(QMainWindow):
    def __init__(self, data_dir=None, auto_start=True, settings=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.settings = settings if settings is not None else recording_settings()
        self.data_dir = recording_directory(data_dir, self.settings)
        self.live_samples = []
        self.live_metadata = {"scope": "system", "cpu_count": psutil.cpu_count() or 1,
                              "physical_cores": psutil.cpu_count(logical=False),
                              "ram_total_mib": psutil.virtual_memory().total / 1024**2,
                              "swap_total_mib": psutil.swap_memory().total / 1024**2}
        self.samples, self.metadata = self.live_samples, self.live_metadata
        self.monitor = SystemMonitor(parent=self)
        self.monitor.sample_ready.connect(self.add_sample)
        self.monitor.recording_changed.connect(self.recording_changed)
        self.monitor.failed.connect(self.monitor_failed)
        self.viewing_live = True
        self.recording = False
        self.cursor_follows = True
        self.manual_y = {}
        self.live_window_seconds = None
        self.chart_epoch = 0
        self.chart_ranges = {}
        self.chart_values = {}
        self.chart_markers = [None, None]
        self.recording_span = {}
        self.record_started_monotonic = None
        self.last_sample_monotonic = None
        self.monitor_error = ""
        self.fullscreen_dialog = None
        self.closing = False
        self.last_recording = None
        self.session_path = None
        self.core_bars = []
        self.setWindowTitle("Chequebo · Live system monitor")
        self.resize(1580, 920)
        self.setStyleSheet("""
          QMainWindow, QDialog, QWidget { background:#161b23; color:#e8eef7; }
          QGroupBox { border:1px solid #394352; border-radius:7px; margin-top:10px; padding-top:12px; }
          QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 4px; }
          QPushButton, QComboBox { background:#283343; border:1px solid #475569; border-radius:5px; padding:7px 12px; }
          QPushButton:hover { background:#35445a; }
          QPushButton:disabled { color:#8190a5; }
          QLineEdit, QDoubleSpinBox { background:#202835; padding:6px; border:1px solid #475569; border-radius:4px; }
          QTableWidget { gridline-color:#394352; alternate-background-color:#202835; }
          QHeaderView::section { background:#283343; padding:7px; border:0; }
          QProgressBar { background:#273242; border:0; border-radius:3px; text-align:center; min-height:18px; }
          QProgressBar::chunk { background:#4a8ed6; border-radius:3px; }
          QScrollArea { border:0; }
          QToolTip { background:#283343; color:#ffffff; border:1px solid #475569; }
        """)
        scroll = self.scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.layout = QVBoxLayout(content)
        self.layout.setSpacing(12)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)
        self._build_launch()
        self._build_time_controls()
        self._build_charts()
        self._build_maxima()
        self._build_processes()
        self.status = QLabel("Starting live system monitoring…")
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        self._core_layout(psutil.cpu_count() or 1)
        self.set_scope()
        self.refresh()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_activity)
        self.clock_timer.start(250)
        if auto_start:
            QTimer.singleShot(0, self.start_monitor)

    def start_monitor(self):
        if not self.closing and not self.monitor.isRunning():
            self.monitor.interval = self.interval.value()
            self.monitor.start()

    def _build_launch(self):
        group = QGroupBox("Chequebo · Your whole computer, live")
        grid = QGridLayout(group)
        hint = QLabel("Monitoring starts automatically. Record saves from now; Stop keeps monitoring. Live history: last 10 minutes.")
        hint.setWordWrap(True)
        grid.addWidget(hint, 0, 0, 1, 3)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 60)
        self.interval.setValue(1)
        self.interval.setSuffix(" s / sample")
        self.interval.valueChanged.connect(self.monitor.set_interval)
        controls = QHBoxLayout()
        self.start_button = QPushButton("● Record")
        self.start_button.setStyleSheet("QPushButton { background:#75343f; } QPushButton:disabled { background:#283343; }")
        self.stop_button = QPushButton("■ Stop recording")
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Saves this recording. Live monitoring continues.")
        self.open_button = QPushButton("Open recording…")
        self.last_button = QPushButton("Open last recording")
        self.last_button.setEnabled(False)
        self.live_button = QPushButton("Back to live")
        self.live_button.setEnabled(False)
        gpu = QPushButton("Check GPU")
        self.process_button = QPushButton('Processes')
        self.process_button.clicked.connect(lambda: self.process_dock.setVisible(not self.process_dock.isVisible()))
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        self.open_button.clicked.connect(self.open_session)
        self.last_button.clicked.connect(lambda: self.load_recording(self.last_recording))
        self.live_button.clicked.connect(self.show_live)
        gpu.clicked.connect(self.check_gpu)
        for button in [self.start_button, self.stop_button, self.open_button, self.last_button, self.live_button, gpu, self.process_button]:
            controls.addWidget(button)
        grid.addLayout(controls, 1, 0, 1, 3)
        self.record_status = QLabel("LIVE · Not recording to disk")
        self.record_status.setWordWrap(True)
        grid.addWidget(self.record_status, 2, 0, 1, 2)
        grid.addWidget(self.interval, 2, 2)
        self.machine = QLabel()
        self.machine.setWordWrap(True)
        grid.addWidget(self.machine, 3, 0, 1, 3)
        self.record_clock = QLabel("Not recording · 00:00:00")
        self.record_clock.setStyleSheet("font-size:18px; font-weight:bold; color:#65df9b;")
        grid.addWidget(self.record_clock, 4, 0, 1, 3)
        self.folder_button = QPushButton("Choose recording folder…")
        self.folder_button.clicked.connect(self.choose_recording_folder)
        self.folder_label = QLabel(str(self.data_dir))
        self.folder_label.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_label.setWordWrap(True)
        self.folder_label.setToolTip("Folder for new recordings. An active recording keeps its current path.")
        grid.addWidget(self.folder_button, 5, 0)
        grid.addWidget(self.folder_label, 5, 1, 1, 2)
        self.layout.addWidget(group)

    def _build_time_controls(self):
        group = QGroupBox("Chart time range")
        row = QHBoxLayout(group)
        self.follow = QCheckBox("Follow latest")
        self.follow.setChecked(True)
        self.follow.setEnabled(False)
        self.follow.setToolTip("Live charts always advance. Zoom changes the visible duration.")
        self.range_mode = QComboBox()
        for label, seconds in [("All available history", 0), ("Last 2 minutes", 120), ("Last 5 minutes", 300), ("Custom window", -1)]:
            self.range_mode.addItem(label, seconds)
        self.window_seconds = QDoubleSpinBox()
        self.window_seconds.setRange(.2, 600)
        self.window_seconds.setValue(60)
        self.window_seconds.setEnabled(False)
        self.window_seconds.setSuffix(" s window")
        self.window_seconds.setToolTip("Visible duration; the newest sample stays at the right edge")
        self.start_time, self.end_time = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.start_time, self.end_time):
            spin.setRange(0, 100000000)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setReadOnly(True)
        for widget in [self.follow, self.range_mode, self.window_seconds, QLabel("From"), self.start_time, QLabel("To"), self.end_time]:
            row.addWidget(widget)
        self.follow.toggled.connect(self.follow_changed)
        self.range_mode.currentIndexChanged.connect(self.preset_changed)
        self.window_seconds.valueChanged.connect(self.window_changed)
        self.start_time.valueChanged.connect(self.manual_range)
        self.end_time.valueChanged.connect(self.manual_range)
        self.layout.addWidget(group)
        self.view_status = QLabel("LIVE · Waiting for the first sample")
        self.view_status.setWordWrap(True)
        self.layout.addWidget(self.view_status)

    def _build_charts(self):
        self.chart_specs = {
            "memory_mib": ("System memory · RAM + swap", "GiB", [("memory_mib", "RAM + swap")]),
            "cpu_percent": ("System CPU · 100% = all logical CPUs", "%", [("cpu_percent", "CPU")]),
            "gpu_percent": ("GPU · whole system", "%", [("gpu_percent", "GPU")]),
            "vram_mib": ("VRAM · whole system", "GiB", [("vram_mib", "VRAM")]),
            "read_mib_s": ("Disk I/O · read / write", "MiB/s", [("read_mib_s", "Read"), ("write_mib_s", "Write")]),
            "net_recv_mbps": ("Network · download / upload", "Mb/s", [("net_recv_mbps", "Download"), ("net_sent_mbps", "Upload")]),
        }
        self.dashboard = PlotlyDashboard(self)
        self.dashboard.setFixedHeight(1174)
        self.dashboard.event_received.connect(self.chart_event)
        self.layout.addWidget(self.dashboard)
        self.dashboard_index = self.layout.indexOf(self.dashboard)
        self.cores_group = QGroupBox("Each logical CPU · whole system · selected time")
        cores_box = QVBoxLayout(self.cores_group)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(200)
        area.setMaximumHeight(270)
        self.core_widget = QWidget()
        self.core_grid = QGridLayout(self.core_widget)
        area.setWidget(self.core_widget)
        cores_box.addWidget(area)
        self.core_time = QLabel("Select a time to inspect logical CPU activity.")
        self.core_time.setWordWrap(True)
        cores_box.addWidget(self.core_time)
        self.layout.addWidget(self.cores_group)
        cursor_row = QHBoxLayout()
        self.cursor = QSlider(Qt.Orientation.Horizontal)
        self.cursor.setRange(0, 0)
        self.cursor.valueChanged.connect(self.cursor_changed)
        self.cursor_label = QLabel("Time: —")
        cursor_row.addWidget(self.cursor_label)
        cursor_row.addWidget(self.cursor, 1)
        self.layout.addLayout(cursor_row)

    def chart_event(self, event):
        if self.closing:
            return
        kind, key = event.get("type"), event.get("key")
        if kind == "scroll":
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.value() + round(event.get("dy", 0)))
        elif kind == "peak":
            self.inspect_peak(event.get('metric'), event.get('elapsed'))
        elif kind == "select":
            self.process_dock.show()
            self.inspector.selected_identity = None
            self.inspector.sort_resource({'memory_mib': 'rss_mib', 'cpu_percent': 'cpu_percent',
                                          'gpu_percent': 'vram_mib', 'vram_mib': 'vram_mib'}.get(key, 'rss_mib'))
            self.select_time(event["elapsed"])
            self.inspector.rebuild()
        elif kind == "fullscreen":
            self.toggle_fullscreen(key)
        elif kind == "restore":
            if self.fullscreen_dialog:
                self.fullscreen_dialog.close()
        elif key in self.chart_specs and kind == "range":
            for axis in ('x', 'y'):
                values = event.get(axis)
                if values is not None and (len(values) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values) or values[1] <= values[0]):
                    return
            if event.get("y"):
                self.manual_y[key] = event["y"]
            if event.get("x"):
                self.set_chart_time_range(*event["x"])
            else:
                self.refresh()
        elif key in self.chart_specs and kind == "action":
            action = event.get("action")
            if action in ("x_in", "x_out", "y_in", "y_out"):
                self.zoom_chart(key, 0 if action[0] == 'x' else 1, .5 if action.endswith('in') else 2)
            elif action == "fit_y": self.fit_y(key)
            elif action == "reset": self.reset_chart(key)
            elif action == "fullscreen": self.toggle_fullscreen(key)

    def _build_processes(self):
        self.process_dock = QDockWidget('Processes at selected time', self)
        self.process_dock.setObjectName('process-inspector')
        self.inspector = ProcessInspector()
        self.inspector.follow_requested.connect(self.follow_processes)
        self.inspector.export_requested.connect(self.export_process_history)
        self.process_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.process_dock)
        self.resizeDocks([self.process_dock], [500], Qt.Orientation.Horizontal)

    def follow_processes(self):
        self.cursor_follows = True
        self.refresh()

    def export_process_history(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export process history', str(self.data_dir / 'chequebo-processes.csv'), 'CSV (*.csv)')
        if path:
            try:
                export_processes(path, self.samples)
            except OSError as exc:
                QMessageBox.warning(self, 'Export failed', str(exc))

    def _build_maxima(self):
        group = self.peaks_group = QGroupBox("Live buffer peaks · last 10 minutes")
        box = QVBoxLayout(group)
        actions = QHBoxLayout()
        maxima_button = QPushButton("Export peak report…")
        data_button = QPushButton("Export samples to CSV…")
        maxima_button.clicked.connect(lambda: self.export(True))
        data_button.clicked.connect(lambda: self.export(False))
        actions.addWidget(maxima_button)
        actions.addWidget(data_button)
        actions.addStretch()
        box.addLayout(actions)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Resource", "Recording peak", "Time", "Range peak", "Time", "Observed at peak"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(320)
        self.table.cellClicked.connect(self.jump_to_peak)
        box.addWidget(self.table)
        note = QLabel("Soft red bands mark the buffer/recording maximum of each chart series. Click a band, peak button or table value for recorded context. Equal maxima use their first sample; band width is not a duration. Range peaks are listed separately. Observed processes are not proof of cause.")
        note.setWordWrap(True)
        box.addWidget(note)
        self.layout.addWidget(group)

    def _core_layout(self, count):
        while self.core_grid.count():
            item = self.core_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.core_bars = []
        columns = 2 if count > 8 else 1
        for index in range(count):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFormat("%p%")
            bar.setValue(0)
            col, row = (index % columns) * 2, index // columns
            self.core_grid.addWidget(QLabel(f"CPU {index}"), row, col)
            self.core_grid.addWidget(bar, row, col + 1)
            self.core_bars.append(bar)
        physical = self.metadata.get("physical_cores", psutil.cpu_count(logical=False))
        self.machine.setText(f"{physical or '?'} physical cores · {count} logical CPUs. 100% total CPU = all {count} logical CPUs fully busy; 1.0 CPU equivalent = the work of one logical CPU at 100%.")

    def set_scope(self):
        self.metrics = metrics_for(self.metadata)
        system = self.metadata.get("scope") == "system"
        titles = {
            "memory_mib": "System memory · RAM + swap" if system else "Program memory · PSS RAM + swap",
            "cpu_percent": "System CPU · 100% = all logical CPUs" if system else "Program CPU · 100% = all logical CPUs",
            "gpu_percent": "GPU · whole system (average across cards)",
            "vram_mib": "VRAM · whole system" if system else "Program + children VRAM · NVIDIA",
            "read_mib_s": "System disk I/O · read / write" if system else "Program disk I/O · read / write",
        }
        for key, title in titles.items():
            _, unit, series = self.chart_specs[key]
            self.chart_specs[key] = (title, unit, series)
        self.peaks_group.setTitle("Live buffer peaks · last 10 minutes" if self.viewing_live else "Recording peaks · whole system" if system else "Recording peaks · program + children (GPU activity is global)")
        self.table.setRowCount(len(self.metrics))
        self.table.setHorizontalHeaderLabels(["Resource", "Buffer peak" if self.viewing_live else "Recording peak", "Time", "Range peak", "Time", "Observed at buffer peak" if self.viewing_live else "Observed at recording peak"])
        for row, (_, title, _) in enumerate(self.metrics):
            for col in range(6):
                self.table.setItem(row, col, QTableWidgetItem(title if col == 0 else "—"))
        self.follow.setEnabled(not self.viewing_live)
        for spin in (self.start_time, self.end_time):
            spin.setReadOnly(self.viewing_live)

    def ram_capacity_gib(self):
        def valid(value):
            return isinstance(value, (int, float)) and math.isfinite(value) and value > 0

        total = self.metadata.get("ram_total_mib")
        if not valid(total):
            # System recordings from 0.3.x already contain capacity in each sample.
            total = next((s["ram_total_mib"] for s in reversed(self.samples)
                          if valid(s.get("ram_total_mib"))), None)
        return total / 1024 if valid(total) else None

    def swap_capacity_gib(self):
        values = [self.metadata.get("swap_total_mib")]
        if not self.viewing_live:
            values.extend(sample.get("swap_total_mib") for sample in self.samples)
        valid = [value for value in values
                 if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0]
        return max(valid) / 1024 if valid else None

    def vram_capacity_gib(self):
        values = [self.metadata.get("vram_total_mib")]
        if not self.viewing_live:
            values.extend(sample.get("vram_total_mib") for sample in self.samples)
        valid = [value for value in values
                 if isinstance(value, (int, float)) and math.isfinite(value) and value > 0]
        return max(valid) / 1024 if valid else None

    def choose_recording_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "Choose recording folder", str(self.data_dir))
        if not selected:
            return
        try:
            folder = Path(selected).expanduser().resolve()
            with tempfile.TemporaryFile(dir=folder, prefix=".chequebo-write-") as probe:
                probe.write(b"Chequebo")
                probe.flush()
        except OSError as exc:
            QMessageBox.warning(self, "Cannot use recording folder",
                                f"Choose a folder you can write to.\n\n{exc}")
            return
        self.data_dir = folder
        self.folder_label.setText(str(folder))
        self.settings.setValue("recording_folder", str(folder))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(self, "Cannot remember recording folder",
                                "This folder will be used for this session, but the preference could not be saved.")

    def new_path(self):
        return self.data_dir / (time.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6] + ".chequebo")

    def start_recording(self):
        if self.recording or not self.monitor.isRunning():
            return
        self.show_live()
        self.start_button.setEnabled(False)
        self.interval.setEnabled(False)
        self.record_status.setText("Starting recording…")
        self.monitor.start_recording(self.new_path())

    def stop_recording(self):
        self.stop_button.setEnabled(False)
        self.record_status.setText("Saving recording… Live monitoring continues.")
        self.monitor.stop_recording()

    def recording_changed(self, path, metadata):
        self.recording = metadata["state"] == "recording"
        self.start_button.setEnabled(not self.recording and self.monitor.isRunning())
        self.stop_button.setEnabled(self.recording)
        self.interval.setEnabled(not self.recording)
        if self.recording:
            self.recording_span = dict(metadata)
            self.record_started_monotonic = metadata.get("started_monotonic", time.monotonic())
            self.record_status.setText(f"● RECORDING whole computer · {path}")
        elif metadata["state"] == "error":
            if "duration" in metadata:
                self.recording_span = dict(metadata)
            self.record_status.setText(f"Recording error: {metadata['error']} · {path}")
        else:
            self.recording_span = dict(metadata)
            self.last_recording = Path(path)
            self.last_button.setEnabled(True)
            self.record_status.setText(f"LIVE · Recording saved: {path} · Open last recording for its complete history and peaks.")
        self.update_record_markers()
        self.update_activity()
        self.render_charts()

    def monitor_failed(self, error):
        self.monitor_error = error
        self.record_status.setText(error)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.update_activity()

    def add_sample(self, sample):
        self.last_sample_monotonic = time.monotonic()
        for key in ("ram_total_mib", "swap_total_mib", "vram_total_mib"):
            if sample.get(key) is not None:
                self.live_metadata[key] = sample[key]
        # A time bound and a hard cap keep memory bounded even for long uptimes.
        removed = 0
        cutoff = sample["elapsed"] - 600
        while self.live_samples and (self.live_samples[0]["elapsed"] < cutoff or len(self.live_samples) >= 3001):
            self.live_samples.pop(0)
            removed += 1
        if self.viewing_live and removed:
            self.cursor.blockSignals(True)
            self.cursor.setValue(max(0, self.cursor.value() - removed))
            self.cursor.blockSignals(False)
        self.live_samples.append(sample)
        if self.viewing_live:
            self.refresh()
            self.status.setText(f"Live · {sample['processes']} system processes · {sample['gpu_note']}")

    def show_live(self):
        if not self.viewing_live:
            self.manual_y.clear()
            self.chart_epoch += 1
        self.live_window_seconds = None
        self.cursor_follows = True
        self.viewing_live = True
        self.samples, self.metadata = self.live_samples, self.live_metadata
        self.live_button.setEnabled(False)
        self.setWindowTitle("Chequebo · Live system monitor")
        self.set_scope()
        self._core_layout(self.metadata["cpu_count"])
        self.range_mode.setCurrentIndex(0)
        self.follow.setChecked(True)
        self.refresh()
        self.status.setText("Live monitoring · last 10 minutes. Recordings contain all samples saved between Record and Stop.")

    def preset_changed(self):
        seconds = self.range_mode.currentData()
        self.live_window_seconds = self.window_seconds.value() if seconds == -1 else (seconds or None)
        self.window_seconds.setEnabled(seconds == -1)
        self.follow.blockSignals(True)
        self.follow.setChecked(True)
        self.follow.blockSignals(False)
        self.refresh()

    def window_changed(self):
        if self.range_mode.currentData() == -1:
            self.live_window_seconds = self.window_seconds.value()
            self.refresh()

    def follow_changed(self, checked):
        if self.viewing_live and not checked:
            self.follow.setChecked(True)
            return
        self.refresh()

    def manual_range(self):
        if self.viewing_live:
            return
        self.follow.blockSignals(True)
        self.follow.setChecked(False)
        self.follow.blockSignals(False)
        self.refresh()

    def bounds(self):
        if self.viewing_live and self.samples:
            end = self.samples[-1]["elapsed"]
            start = self.samples[0]["elapsed"]
            if self.live_window_seconds is not None:
                start = max(start, end - self.live_window_seconds)
            return (min(start, end - .001), end)
        return sorted((self.start_time.value(), self.end_time.value()))

    def update_activity(self):
        if self.recording and self.record_started_monotonic is not None:
            duration = time.monotonic() - self.record_started_monotonic
            self.record_clock.setText(f"● Recording · {clock(duration)}")
        else:
            duration = self.recording_span.get("duration", 0)
            self.record_clock.setText(f"{'Last recording' if self.recording_span else 'Not recording'} · {clock(duration)}")
        if self.monitor_error:
            activity = self.monitor_error
        elif self.last_sample_monotonic is None:
            activity = "Waiting for the first sample"
        else:
            age = time.monotonic() - self.last_sample_monotonic
            activity = (f"No new samples for {age:.0f}s" if age > max(5, self.interval.value() * 3)
                        else f"Receiving data · latest {clock(self.live_samples[-1]['elapsed'])}")
        if not self.viewing_live:
            view = "SAVED RECORDING · live monitoring continues in the background"
        elif self.viewing_live:
            view = "LIVE · newest sample stays on the right, at every zoom level"
            if not self.cursor_follows:
                view += " · inspecting a selected sample"
        else:
            view = "SAVED RECORDING · selected time range"
        self.view_status.setText(f"{view} · {activity}")
        self.live_button.setEnabled(not self.viewing_live or not self.follow.isChecked() or not self.cursor_follows)
        if self.fullscreen_dialog:
            self.fullscreen_status.setText(f"{self.record_clock.text()} · {self.view_status.text()}")

    def update_record_markers(self):
        span = self.recording_span if self.viewing_live else self.metadata
        start = span.get("live_started_elapsed") if self.viewing_live else (0 if span else None)
        stop = span.get("live_ended_elapsed") if self.viewing_live else span.get("duration")
        if not self.viewing_live and stop is None and span.get("ended_at") is not None and span.get("started_at") is not None:
            stop = max(0, span["ended_at"] - span["started_at"])
        self.chart_markers = [start, stop]

    def set_chart_time_range(self, start, end):
        if self.viewing_live:
            self.live_window_seconds = min(600, max(.2, end - start))
            self.range_mode.blockSignals(True)
            self.range_mode.setCurrentIndex(3)
            self.range_mode.blockSignals(False)
            self.window_seconds.blockSignals(True)
            self.window_seconds.setValue(self.live_window_seconds)
            self.window_seconds.blockSignals(False)
            self.window_seconds.setEnabled(True)
        else:
            self.follow.blockSignals(True)
            self.follow.setChecked(False)
            self.follow.blockSignals(False)
            for spin, value in [(self.start_time, max(0, start)), (self.end_time, max(.2, end))]:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self.refresh()

    def chart_points(self, key, selected):
        keys = [item[0] for item in self.chart_specs[key][2]]
        # Keep all points in ordinary live use. Long recordings preserve local extrema.
        if len(selected) > 1800:
            stride = math.ceil(len(selected) / 400)
            indices = {0, len(selected) - 1}
            for offset in range(0, len(selected), stride):
                indices.add(offset)
                for metric in keys:
                    valid = [(i, metric_value(selected[i], metric)) for i in range(offset, min(offset + stride, len(selected)))]
                    valid = [(i, v) for i, v in valid if isinstance(v, (int, float)) and math.isfinite(v)]
                    if valid:
                        indices.add(min(valid, key=lambda pair: pair[1])[0])
                        indices.add(max(valid, key=lambda pair: pair[1])[0])
            selected = [selected[i] for i in sorted(indices)]
        def value(sample, metric):
            number = metric_value(sample, metric)
            if not isinstance(number, (int, float)) or not math.isfinite(number): return None
            return number / 1024 if key in ("memory_mib", "vram_mib") else number
        return [sample["elapsed"] for sample in selected], [{"name": name, "y": [value(sample, metric) for sample in selected]} for metric, name in self.chart_specs[key][2]]

    def zoom_chart(self, key, axis, factor):
        low, high = self.chart_ranges[key][axis]
        width = max(.2 if axis == 0 else .001, (high - low) * factor)
        if axis == 0:
            end = self.bounds()[1]
            self.set_chart_time_range(end - width, end)
        else:
            center = (low + high) / 2
            low = max(0, center - width / 2)
            self.manual_y[key] = [low, low + width]
            self.refresh()

    def fit_y(self, key):
        start, end = self.bounds()
        selected = [sample for sample in self.samples if start <= sample['elapsed'] <= end]
        _, series = self.chart_points(key, selected)
        values = [v for trace in series for v in trace['y'] if v is not None]
        if values:
            margin = max(.05, (max(values) - min(values)) * .1, max(values) * .02)
            self.manual_y[key] = [max(0, min(values) - margin), max(values) + margin]
            self.refresh()

    def reset_chart(self, key):
        self.manual_y.pop(key, None)
        self.chart_epoch += 1
        self.cursor_follows = True
        self.live_window_seconds = None
        self.range_mode.setCurrentIndex(0)
        self.follow.setChecked(True)
        self.refresh()

    def toggle_fullscreen(self, key):
        if self.fullscreen_dialog:
            self.fullscreen_dialog.close()
            return
        if key not in self.chart_specs:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.chart_specs[key][0] + " · Chequebo")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        self.fullscreen_status = QLabel()
        self.fullscreen_status.setWordWrap(True)
        layout.addWidget(self.fullscreen_status)
        restore = QPushButton("Restore dashboard (Esc)")
        restore.clicked.connect(dialog.close)
        layout.addWidget(restore)
        self.layout.removeWidget(self.dashboard)
        layout.addWidget(self.dashboard, 1)
        self.dashboard.setMinimumHeight(200)
        self.dashboard.setMaximumHeight(16777215)
        self.fullscreen_dialog = dialog
        dialog.finished.connect(lambda _: self.restore_chart(key, dialog))
        self.dashboard.fullscreen(key)
        self.update_activity()
        dialog.showFullScreen()

    def restore_chart(self, key, dialog):
        dialog.layout().removeWidget(self.dashboard)
        self.layout.insertWidget(self.dashboard_index, self.dashboard)
        self.dashboard.setFixedHeight(1078)
        self.dashboard.fullscreen()
        self.dashboard.show()
        self.fullscreen_dialog = None

    def refresh(self):
        self.update_record_markers()
        self.update_activity()
        if self.samples and (self.viewing_live or self.follow.isChecked()):
            last = self.samples[-1]["elapsed"]
            if not self.viewing_live:
                last = max(last, self.metadata.get("duration", last))
            earliest = self.samples[0]["elapsed"] if self.viewing_live else 0
            width = self.live_window_seconds
            start = max(earliest, last - width) if width else earliest
            for spin, value in [(self.start_time, start), (self.end_time, last)]:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self.cursor.blockSignals(True)
        self.cursor.setRange(0, max(0, len(self.samples) - 1))
        if self.cursor_follows:
            self.cursor.setValue(max(0, len(self.samples) - 1))
        self.cursor.blockSignals(False)
        self.update_cursor(self.cursor.value(), render=False)
        self.render_charts()
        self.update_maxima()

    def render_charts(self):
        start, end = self.bounds()
        if end <= start: end = start + 1
        selected = [sample for sample in self.samples if start - .05 <= sample['elapsed'] <= end + .05]
        charts = []
        for key, (title, unit, metrics) in self.chart_specs.items():
            x, series = self.chart_points(key, selected)
            values = [v for trace in series for v in trace['y'] if v is not None]
            capacity = None
            if key == 'memory_mib':
                ram, swap = self.ram_capacity_gib(), self.swap_capacity_gib()
                if ram is not None and swap is not None: capacity = ram + swap
            elif key == 'vram_mib': capacity = self.vram_capacity_gib()
            elif key in ('cpu_percent', 'gpu_percent'): capacity = 100
            yrange = self.manual_y.get(key, [0, capacity if capacity is not None else max(1, max(values, default=0) * 1.1)])
            ticks = None
            if capacity and key in ('memory_mib', 'vram_mib') and key not in self.manual_y:
                step = 2 ** math.ceil(math.log2(capacity / 4))
                ticks = [i * step for i in range(math.ceil(capacity / step)) if i * step < capacity - step * .35] + [capacity]
            self.chart_ranges[key] = [[start, end], list(yrange)]
            # Calculate against original samples, before decimation or zoom.
            peaks = [describe_peak(peak, metric, name, unit, self.peak_scope())
                     for metric, name in metrics if (peak := maximum(self.samples, metric)) is not None]
            charts.append(dict(key=key, title=title, unit=unit, x=x, series=series, yRange=yrange, ticks=ticks,
                               ramLimit=self.ram_capacity_gib() if key == 'memory_mib' else None,
                               value=self.chart_values.get(key, 'No samples'), peaks=peaks))
        cursor = self.samples[min(self.cursor.value(), len(self.samples) - 1)]['elapsed'] if self.samples else None
        self.dashboard.render(dict(charts=charts, range=[start, end], live=self.viewing_live,
                                   earliest=self.samples[0]['elapsed'] if self.samples else 0,
                                   cursor=cursor, cursorFollows=self.cursor_follows,
                                   markers=self.chart_markers, epoch=self.chart_epoch))

    def update_cursor(self, index, render=True):
        if not self.samples:
            return
        sample = self.samples[min(index, len(self.samples) - 1)]
        self.inspector.inspect(sample, self.cursor_follows, self.viewing_live)
        self.cursor_label.setText("Time: " + clock(sample["elapsed"]))
        for key in self.chart_specs:
            value = metric_value(sample, key)
            display, unit = display_metric(key, value, self.chart_specs[key][1])
            label = "Unavailable" if display is None else f"{display:.2f} {unit}"
            if key == "memory_mib" and display is not None:
                ram, swap = self.ram_capacity_gib(), self.swap_capacity_gib()
                total = f" / {ram + swap:.2f}" if ram is not None and swap is not None else ""
                label = (f"{display:.2f}{total} GiB used · "
                         f"RAM {sample['ram_mib'] / 1024:.2f} + swap {sample['swap_mib'] / 1024:.2f} GiB")
                if not total:
                    label += " · total capacity unavailable in this recording"
            if key == "vram_mib" and display is not None:
                capacity = self.vram_capacity_gib()
                label = (f"{display:.2f} / {capacity:.2f} GiB used" if capacity is not None
                         else f"{display:.2f} GiB used · GPU capacity unavailable")
            if key == "cpu_percent" and value is not None and sample.get("cpu_equivalent") is not None:
                label += f" of total capacity · {sample['cpu_equivalent']:.2f} CPU equivalents"
            if key == "read_mib_s":
                write = sample.get("write_mib_s")
                label = f"Read: {label} · write: " + (f"{write:.2f} MiB/s" if write is not None else "unavailable")
            if key == "net_recv_mbps":
                upload = sample.get("net_sent_mbps")
                label = "Download: " + label + " · upload: " + (f"{upload:.2f} Mb/s" if upload is not None else "unavailable")
                label += " · includes Internet and LAN"
            self.chart_values[key] = label
        cpus = sample.get("cpu_system", [])
        if cpus and len(cpus) != len(self.core_bars):
            self._core_layout(len(cpus))
        for i, bar in enumerate(self.core_bars):
            available = i < len(cpus)
            bar.setValue(round(cpus[i]) if available else 0)
            bar.setFormat("%p%" if available else "—")
        self.core_time.setText(f"{clock(sample['elapsed'])} · whole-system activity, including other programs. Each bar ranges from 0 to 100%.")
        if render:
            self.render_charts()

    def select_time(self, elapsed):
        self.cursor_follows = False
        times = [sample["elapsed"] for sample in self.samples]
        index = bisect.bisect_left(times, elapsed)
        if index >= len(times):
            index = len(times) - 1
        elif index and elapsed - times[index - 1] < times[index] - elapsed:
            index -= 1
        self.cursor.setValue(index)
        self.update_cursor(index)
        self.update_activity()

    def cursor_changed(self, index):
        self.cursor_follows = False
        self.update_cursor(index)
        self.update_activity()

    def update_maxima(self):
        start, end = self.bounds()
        for row, (key, title, unit) in enumerate(self.metrics):
            for base, peak in [(1, maximum(self.samples, key)),
                               (3, maximum(self.samples, key, start - 0.05, end + 0.05))]:
                description = describe_peak(peak, key, title, unit, self.peak_scope() if base == 1 else 'Range peak') if peak else None
                display, display_unit = display_metric(key, peak[key] if peak else None, unit)
                value = f"{display:.2f} {display_unit}".strip() if peak else "Unavailable"
                instant = clock(peak["elapsed"]) if peak else "—"
                for col, text in [(base, value), (base + 1, instant)]:
                    self.table.item(row, col).setText(text)
                    self.table.item(row, col).setData(Qt.ItemDataRole.UserRole, peak["elapsed"] if peak else None)
                    self.table.item(row, col).setToolTip(description['label'] + '\n\n' + description['context']['details'] if description else '')
                if base == 1:
                    item = self.table.item(row, 5)
                    item.setText(description['context']['summary'] if description else 'Unavailable')
                    item.setToolTip(description['context']['details'] if description else '')
                    item.setData(Qt.ItemDataRole.UserRole, peak['elapsed'] if peak else None)

    def peak_scope(self):
        return 'Buffer peak' if self.viewing_live else 'Recording peak'

    def inspect_peak(self, key, elapsed, scope=None):
        metric = next((item for item in self.metrics if item[0] == key), None)
        # An old click may arrive after its live sample expired. Never substitute
        # a different sample and claim it explains the original peak.
        sample = next((s for s in self.samples if s['elapsed'] == elapsed), None)
        if metric is None or sample is None:
            return
        value = metric_value(sample, key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        peak = describe_peak(sample, key, metric[1], metric[2], scope or self.peak_scope())
        if self.fullscreen_dialog:
            self.fullscreen_dialog.close()
        self.inspector.selected_identity = None
        self.inspector.filter.clear()
        self.inspector.sort_resource(peak['context']['sort'])
        self.process_dock.show()
        start, end = self.bounds()
        if not start <= elapsed <= end:
            self.live_window_seconds = None
            self.range_mode.setCurrentIndex(0)
            self.follow.setChecked(True)
            self.refresh()
        self.select_time(elapsed)
        self.inspector.rebuild()
        self.inspector.show_peak(peak)

    def jump_to_peak(self, row, col):
        if col == 0:
            return
        elapsed = self.table.item(row, col).data(Qt.ItemDataRole.UserRole)
        if elapsed is not None:
            self.inspect_peak(self.metrics[row][0], elapsed, 'Range peak' if col in (3, 4) else self.peak_scope())

    def open_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open recording", str(self.data_dir), "Chequebo recordings (*.chequebo)")
        if path:
            self.load_recording(path)

    def load_recording(self, path):
        try:
            metadata, samples = load_session(path)
            self.metadata, self.samples = metadata, samples
            self.inspector.selected_identity = None
            self.viewing_live = False
            self.manual_y.clear()
            self.live_window_seconds = None
            self.chart_epoch += 1
            self.cursor_follows = True
            self.session_path = Path(path)
            self.live_button.setEnabled(True)
            self.setWindowTitle(f"Chequebo · {Path(path).name}")
            self.set_scope()
            self._core_layout(self.metadata.get("cpu_count", psutil.cpu_count() or 1))
            self.range_mode.setCurrentIndex(0)
            self.follow.setChecked(True)
            self.refresh()
            state = self.metadata.get("state", "unknown")
            warning = " · unfinished recording: showing samples saved so far" if state == "recording" else ""
            self.status.setText(f"Recording opened{warning}: {path}\nLive monitoring continues in the background. Click Back to live to return.")
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Unable to open", str(exc))

    def export(self, maxima):
        if not self.samples:
            QMessageBox.information(self, "No data", "Wait for live samples or open a recording first.")
            return
        default = self.data_dir / ("chequebo-peaks.csv" if maxima else "chequebo-samples.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(default), "CSV (*.csv)")
        if path:
            try:
                if maxima:
                    start, end = self.bounds()
                    export_maxima(path, self.samples, start - 0.05, end + 0.05, self.metadata)
                else:
                    export_samples(path, self.samples, self.metadata)
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def check_gpu(self):
        gpu = GPU()
        try:
            usage, memory_used = gpu.sample()
            details = [gpu.note]
            if gpu.nv:
                for handle in gpu.handles:
                    name = gpu.nv.nvmlDeviceGetName(handle)
                    memory = gpu.nv.nvmlDeviceGetMemoryInfo(handle)
                    details.append(f"{name} · {memory.total / 1024**3:.1f} GiB VRAM")
            details.append(f"Whole-system activity: {usage:.0f} %" if usage is not None else "GPU activity is unavailable.")
            details.append(f"Whole-system VRAM used: {memory_used:.1f} MiB" if memory_used is not None else "GPU memory is unavailable.")
            if self.monitor.isRunning():
                self.monitor.retry_gpu()
            QMessageBox.information(self, "GPU diagnostics · Chequebo", "\n\n".join(details))
        except Exception as exc:
            QMessageBox.warning(self, "GPU diagnostics", str(exc))
        finally:
            gpu.close()

    def closeEvent(self, event):
        self.closing = True
        if self.monitor.isRunning():
            self.monitor.stop()
            if not self.monitor.wait(5000):
                self.status.setText("Finishing the recording; try closing again in a few seconds.")
                event.ignore()
                return
        if self.fullscreen_dialog:
            self.fullscreen_dialog.close()
        self.clock_timer.stop()
        self.dashboard.dispose()
        event.accept()
