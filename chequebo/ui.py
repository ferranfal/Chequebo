"""Qt window. Sampling and persistence run outside the UI thread."""

import bisect
import math
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import psutil
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox,
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .metrics import GPU
from .monitor import SystemMonitor
from .session import clock, export_maxima, export_samples, load_session, maximum, metrics_for


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [clock(value) + (f".{int(value * 10) % 10}" if spacing < 1 else "") for value in values]


class Window(QMainWindow):
    def __init__(self, data_dir, auto_start=True):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.live_samples = []
        self.live_metadata = {"scope": "system", "cpu_count": psutil.cpu_count() or 1,
                              "physical_cores": psutil.cpu_count(logical=False)}
        self.samples, self.metadata = self.live_samples, self.live_metadata
        self.monitor = SystemMonitor(parent=self)
        self.monitor.sample_ready.connect(self.add_sample)
        self.monitor.recording_changed.connect(self.recording_changed)
        self.monitor.failed.connect(self.monitor_failed)
        self.viewing_live = True
        self.recording = False
        self.closing = False
        self.last_recording = None
        self.session_path = None
        self.plots, self.curves, self.lines, self.value_labels = {}, {}, {}, {}
        self.chart_groups = {}
        self.core_bars = []
        self.setWindowTitle("Chequebo · Live system monitor")
        self.resize(1220, 920)
        pg.setConfigOptions(antialias=True, background="#151a22", foreground="#dce5f1")
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
        scroll = QScrollArea()
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
        self.status = QLabel("Starting live system monitoring…")
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        self._core_layout(psutil.cpu_count() or 1)
        self.set_scope()
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
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        self.open_button.clicked.connect(self.open_session)
        self.last_button.clicked.connect(lambda: self.load_recording(self.last_recording))
        self.live_button.clicked.connect(self.show_live)
        gpu.clicked.connect(self.check_gpu)
        for button in [self.start_button, self.stop_button, self.open_button, self.last_button, self.live_button, gpu]:
            controls.addWidget(button)
        grid.addLayout(controls, 1, 0, 1, 3)
        self.record_status = QLabel("LIVE · Not recording to disk")
        self.record_status.setWordWrap(True)
        grid.addWidget(self.record_status, 2, 0, 1, 2)
        grid.addWidget(self.interval, 2, 2)
        self.machine = QLabel()
        self.machine.setWordWrap(True)
        grid.addWidget(self.machine, 3, 0, 1, 3)
        self.layout.addWidget(group)

    def _build_time_controls(self):
        group = QGroupBox("Chart time range")
        row = QHBoxLayout(group)
        self.follow = QCheckBox("Follow latest")
        self.follow.setChecked(True)
        self.range_mode = QComboBox()
        for label, seconds in [("All available history", 0), ("Last 2 minutes", 120), ("Last 5 minutes", 300)]:
            self.range_mode.addItem(label, seconds)
        self.range_mode.addItem("Custom range", -1)
        self.start_time, self.end_time = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in [self.start_time, self.end_time]:
            spin.setRange(0, 100000000)
            spin.setDecimals(1)
            spin.setSuffix(" s")
        self.start_time.setToolTip("Seconds since monitoring started (or since the opened recording started)")
        self.end_time.setToolTip("Seconds since monitoring started (or since the opened recording started)")
        for widget in [self.follow, self.range_mode, QLabel("From"), self.start_time, QLabel("To"), self.end_time]:
            row.addWidget(widget)
        self.follow.toggled.connect(lambda _: self.refresh())
        self.range_mode.currentIndexChanged.connect(self.preset_changed)
        self.start_time.valueChanged.connect(self.manual_range)
        self.end_time.valueChanged.connect(self.manual_range)
        self.layout.addWidget(group)

    def _build_charts(self):
        grid = QGridLayout()
        self.layout.addLayout(grid)
        chart_specs = [
            ("ram_mib", "Program + children RAM · PSS memory", "MiB"),
            ("cpu_percent", "Program CPU · 100% = all logical CPUs", "%"),
            ("gpu_percent", "GPU · whole system (average across cards)", "%"),
            ("vram_mib", "Program + children VRAM · NVIDIA", "MiB"),
            ("read_mib_s", "Program disk I/O · read / write", "MiB/s"),
        ]
        for index, (key, title, unit) in enumerate(chart_specs):
            group = QGroupBox(title)
            self.chart_groups[key] = group
            box = QVBoxLayout(group)
            label = QLabel("No samples")
            label.setWordWrap(True)
            self.value_labels[key] = label
            box.addWidget(label)
            plot = pg.PlotWidget(axisItems={"bottom": TimeAxis(orientation="bottom")})
            plot.setFixedHeight(180)
            plot.setLabel("left", unit)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.setLabel("bottom", "Elapsed time")
            plot.showGrid(x=True, y=True, alpha=0.15)
            plot.setMouseEnabled(x=False, y=False)
            plot.setMenuEnabled(False)
            if key in ("cpu_percent", "gpu_percent"):
                plot.setYRange(0, 100, padding=0.03)
            self.plots[key] = plot
            self.curves[key] = plot.plot(pen=pg.mkPen("#79b8ff", width=2), connect="finite")
            self.curves[key].setDownsampling(auto=True, method="peak")
            self.curves[key].setClipToView(True)
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#9aaec6"))
            plot.addItem(line)
            self.lines[key] = line
            if key == "read_mib_s":
                self.curves["write_mib_s"] = plot.plot(pen=pg.mkPen("#e4b76d", width=2), connect="finite")
                self.curves["write_mib_s"].setDownsampling(auto=True, method="peak")
                self.curves["write_mib_s"].setClipToView(True)
            plot.scene().sigMouseClicked.connect(lambda event, p=plot: self.chart_clicked(p, event))
            box.addWidget(plot)
            grid.addWidget(group, index // 2, index % 2)
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
        grid.addWidget(self.cores_group, 2, 1)
        cursor_row = QHBoxLayout()
        self.cursor = QSlider(Qt.Orientation.Horizontal)
        self.cursor.setRange(0, 0)
        self.cursor.valueChanged.connect(self.update_cursor)
        self.cursor.sliderPressed.connect(lambda: self.follow.setChecked(False))
        self.cursor_label = QLabel("Time: —")
        cursor_row.addWidget(self.cursor_label)
        cursor_row.addWidget(self.cursor, 1)
        self.layout.addLayout(cursor_row)

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
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Resource", "Recording peak", "Time", "Range peak", "Time"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(320)
        self.table.cellClicked.connect(self.jump_to_peak)
        box.addWidget(self.table)
        note = QLabel("Click a peak to inspect it. Peaks are sampled: brief spikes can occur between samples. Each resource may peak at a different time.")
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
            "ram_mib": "System RAM · total minus available" if system else "Program + children RAM · PSS memory",
            "cpu_percent": "System CPU · 100% = all logical CPUs" if system else "Program CPU · 100% = all logical CPUs",
            "gpu_percent": "GPU · whole system (average across cards)",
            "vram_mib": "VRAM · whole system" if system else "Program + children VRAM · NVIDIA",
            "read_mib_s": "System disk I/O · read / write" if system else "Program disk I/O · read / write",
        }
        for key, title in titles.items():
            self.chart_groups[key].setTitle(title)
        self.peaks_group.setTitle("Live buffer peaks · last 10 minutes" if self.viewing_live else "Recording peaks · whole system" if system else "Recording peaks · program + children (GPU activity is global)")
        self.table.setRowCount(len(self.metrics))
        self.table.setHorizontalHeaderLabels(["Resource", "Buffer peak" if self.viewing_live else "Recording peak", "Time", "Range peak", "Time"])
        for row, (_, title, _) in enumerate(self.metrics):
            for col in range(5):
                self.table.setItem(row, col, QTableWidgetItem(title if col == 0 else "—"))

    def new_path(self):
        return self.data_dir / (time.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6] + ".chequebo")

    def start_recording(self):
        if self.recording or not self.monitor.isRunning():
            return
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
            self.record_status.setText(f"● RECORDING whole computer · {path}")
        elif metadata["state"] == "error":
            self.record_status.setText(f"Recording error: {metadata['error']} · {path}")
        else:
            self.last_recording = Path(path)
            self.last_button.setEnabled(True)
            self.record_status.setText(f"LIVE · Recording saved: {path} · Open last recording for its complete history and peaks.")

    def monitor_failed(self, error):
        self.record_status.setText(error)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)

    def add_sample(self, sample):
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
        self.viewing_live = True
        self.samples, self.metadata = self.live_samples, self.live_metadata
        self.live_button.setEnabled(False)
        self.setWindowTitle("Chequebo · Live system monitor")
        self.set_scope()
        self._core_layout(self.metadata["cpu_count"])
        self.range_mode.setCurrentIndex(0)
        self.follow.setChecked(True)
        self.cursor.setValue(len(self.samples) - 1)
        self.refresh()
        self.status.setText("Live monitoring · last 10 minutes. Recordings contain all samples saved between Record and Stop.")

    def preset_changed(self):
        if self.range_mode.currentData() == -1:
            self.follow.setChecked(False)
        else:
            self.follow.setChecked(True)
        self.refresh()

    def manual_range(self):
        self.follow.blockSignals(True)
        self.follow.setChecked(False)
        self.follow.blockSignals(False)
        self.range_mode.blockSignals(True)
        self.range_mode.setCurrentIndex(3)
        self.range_mode.blockSignals(False)
        self.refresh()

    def bounds(self):
        return sorted((self.start_time.value(), self.end_time.value()))

    def refresh(self):
        if not self.samples:
            for curve in self.curves.values():
                curve.setData([], [])
            for label in self.value_labels.values():
                label.setText("No samples")
            self.cursor.setRange(0, 0)
            self.cursor_label.setText("Time: —")
            for bar in self.core_bars:
                bar.setValue(0)
                bar.setFormat("—")
            for row in range(len(self.metrics)):
                for col in range(1, 5):
                    self.table.item(row, col).setText("—")
                    self.table.item(row, col).setData(Qt.ItemDataRole.UserRole, None)
            return
        if self.follow.isChecked():
            last = self.samples[-1]["elapsed"]
            window = self.range_mode.currentData()
            earliest = self.samples[0]["elapsed"] if self.viewing_live else 0
            start = max(earliest, last - window) if window and window > 0 else earliest
            for spin, value in [(self.start_time, start), (self.end_time, last)]:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        start, end = self.bounds()
        # Allow for the selector precision without dropping the latest sample.
        selected = [s for s in self.samples if start - 0.05 <= s["elapsed"] <= end + 0.05]
        x = np.array([s["elapsed"] for s in selected])
        for key, curve in self.curves.items():
            y = np.array([s[key] if s.get(key) is not None else np.nan for s in selected])
            curve.setData(x, y)
        for key, plot in self.plots.items():
            plot.setXRange(start, max(end, start + 1), padding=0)
            if key not in ("cpu_percent", "gpu_percent"):
                keys = (key, "write_mib_s") if key == "read_mib_s" else (key,)
                values = [sample[k] for sample in selected for k in keys
                          if isinstance(sample.get(k), (int, float)) and math.isfinite(sample[k])]
                plot.setYRange(0, max(1, max(values, default=0) * 1.1), padding=0)
        self.cursor.blockSignals(True)
        self.cursor.setRange(0, len(self.samples) - 1)
        if self.follow.isChecked():
            self.cursor.setValue(len(self.samples) - 1)
        self.cursor.blockSignals(False)
        self.update_cursor(self.cursor.value())
        self.update_maxima()

    def update_cursor(self, index):
        if not self.samples:
            return
        sample = self.samples[min(index, len(self.samples) - 1)]
        self.cursor_label.setText("Time: " + clock(sample["elapsed"]))
        for key, line in self.lines.items():
            line.setPos(sample["elapsed"])
            value = sample.get(key)
            label = "Unavailable" if value is None else f"{value:.2f} {dict((k,u) for k,_,u in self.metrics)[key]}"
            if key == "cpu_percent" and value is not None and sample.get("cpu_equivalent") is not None:
                label += f" of total capacity · {sample['cpu_equivalent']:.2f} CPU equivalents"
            if key == "read_mib_s":
                write = sample.get("write_mib_s")
                label = f"Read: {label} · write: " + (f"{write:.2f} MiB/s" if write is not None else "unavailable")
            self.value_labels[key].setText(label)
        cpus = sample.get("cpu_system", [])
        if cpus and len(cpus) != len(self.core_bars):
            self._core_layout(len(cpus))
        for i, bar in enumerate(self.core_bars):
            available = i < len(cpus)
            bar.setValue(round(cpus[i]) if available else 0)
            bar.setFormat("%p%" if available else "—")
        self.core_time.setText(f"{clock(sample['elapsed'])} · whole-system activity, including other programs. Each bar ranges from 0 to 100%.")

    def chart_clicked(self, plot, event):
        if not self.samples or not plot.sceneBoundingRect().contains(event.scenePos()):
            return
        elapsed = plot.plotItem.vb.mapSceneToView(event.scenePos()).x()
        self.select_time(elapsed)

    def select_time(self, elapsed):
        self.follow.setChecked(False)
        times = [sample["elapsed"] for sample in self.samples]
        index = bisect.bisect_left(times, elapsed)
        if index >= len(times):
            index = len(times) - 1
        elif index and elapsed - times[index - 1] < times[index] - elapsed:
            index -= 1
        self.cursor.setValue(index)
        self.update_cursor(index)

    def update_maxima(self):
        start, end = self.bounds()
        for row, (key, _, unit) in enumerate(self.metrics):
            for base, peak in [(1, maximum(self.samples, key)),
                               (3, maximum(self.samples, key, start - 0.05, end + 0.05))]:
                value = f"{peak[key]:.2f} {unit}".strip() if peak else "Unavailable"
                instant = clock(peak["elapsed"]) if peak else "—"
                for col, text in [(base, value), (base + 1, instant)]:
                    self.table.item(row, col).setText(text)
                    self.table.item(row, col).setData(Qt.ItemDataRole.UserRole, peak["elapsed"] if peak else None)

    def jump_to_peak(self, row, col):
        if col == 0:
            return
        elapsed = self.table.item(row, col).data(Qt.ItemDataRole.UserRole)
        if elapsed is not None:
            start, end = self.bounds()
            if not start <= elapsed <= end:
                self.range_mode.setCurrentIndex(0)
                self.follow.setChecked(True)
                self.refresh()
            self.follow.setChecked(False)
            self.select_time(elapsed)

    def open_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open recording", str(self.data_dir), "Chequebo recordings (*.chequebo)")
        if path:
            self.load_recording(path)

    def load_recording(self, path):
        try:
            metadata, samples = load_session(path)
            self.metadata, self.samples = metadata, samples
            self.viewing_live = False
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
        event.accept()
