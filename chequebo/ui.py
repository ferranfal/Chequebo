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
    QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .metrics import GPU
from .monitor import SystemMonitor
from .session import clock, display_metric, export_maxima, export_samples, load_session, maximum, metrics_for, metric_value


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [clock(value) + (f".{int(value * 10) % 10}" if spacing < 1 else "") for value in values]


class MemoryAxis(pg.AxisItem):
    def boundingRect(self):
        # Keep zero and the exact capacity visible at the edges, including with a grid.
        return super().boundingRect().adjusted(0, -15, 0, 15)


class Window(QMainWindow):
    def __init__(self, data_dir, auto_start=True):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
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
        self.manual_y = set()
        self.recording_span = {}
        self.record_started_monotonic = None
        self.last_sample_monotonic = None
        self.monitor_error = ""
        self.fullscreen_dialog = None
        self.chart_buttons, self.record_lines = {}, {}
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
        self.record_clock = QLabel("Not recording · 00:00:00")
        self.record_clock.setStyleSheet("font-size:18px; font-weight:bold; color:#65df9b;")
        grid.addWidget(self.record_clock, 4, 0, 1, 3)
        self.layout.addWidget(group)

    def _build_time_controls(self):
        group = QGroupBox("Chart time range")
        row = QHBoxLayout(group)
        self.follow = QCheckBox("Follow live time")
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
        self.follow.toggled.connect(self.follow_changed)
        self.range_mode.currentIndexChanged.connect(self.preset_changed)
        self.start_time.valueChanged.connect(self.manual_range)
        self.end_time.valueChanged.connect(self.manual_range)
        self.layout.addWidget(group)
        self.view_status = QLabel("LIVE · Waiting for the first sample")
        self.view_status.setWordWrap(True)
        self.layout.addWidget(self.view_status)

    def _build_charts(self):
        grid = self.chart_grid = QGridLayout()
        self.layout.addLayout(grid)
        chart_specs = [
            ("memory_mib", "System memory · RAM + swap", "GiB"),
            ("cpu_percent", "Program CPU · 100% = all logical CPUs", "%"),
            ("gpu_percent", "GPU · whole system (average across cards)", "%"),
            ("vram_mib", "Program + children VRAM · NVIDIA", "GiB"),
            ("read_mib_s", "Program disk I/O · read / write", "MiB/s"),
        ]
        for index, (key, title, unit) in enumerate(chart_specs):
            group = QGroupBox(title)
            self.chart_groups[key] = group
            box = QVBoxLayout(group)
            toolbar = QHBoxLayout()
            toolbar.setSpacing(4)
            self.chart_buttons[key] = {}
            for action, text, tip, callback in [
                ("x_in", "X+", "Zoom in horizontally", lambda _, k=key: self.zoom_chart(k, 0, 0.5)),
                ("x_out", "X−", "Zoom out horizontally", lambda _, k=key: self.zoom_chart(k, 0, 2)),
                ("y_in", "Y+", "Zoom in vertically around the selected value", lambda _, k=key: self.zoom_chart(k, 1, 0.5)),
                ("y_out", "Y−", "Zoom out vertically", lambda _, k=key: self.zoom_chart(k, 1, 2)),
                ("fit_y", "Fit Y", "Fit the vertical scale to visible data", lambda _, k=key: self.fit_y(k)),
                ("reset", "Reset", "Restore time range and default scale", lambda _, k=key: self.reset_chart(k)),
                ("fullscreen", "⛶ Full screen", "Expand this chart; Esc restores the dashboard", lambda _, k=key: self.toggle_fullscreen(k)),
            ]:
                button = QToolButton()
                button.setText(text)
                button.setToolTip(tip)
                button.setAccessibleName(tip)
                button.clicked.connect(callback)
                self.chart_buttons[key][action] = button
                toolbar.addWidget(button)
            toolbar.addStretch()
            box.addLayout(toolbar)
            label = QLabel("No samples")
            label.setWordWrap(True)
            self.value_labels[key] = label
            box.addWidget(label)
            axes = {"bottom": TimeAxis(orientation="bottom")}
            if key in ("memory_mib", "vram_mib"):
                axes["left"] = MemoryAxis(orientation="left")
            plot = pg.PlotWidget(axisItems=axes)
            if key in ("memory_mib", "vram_mib"):
                plot.plotItem.layout.setContentsMargins(1, 12, 1, 1)
            plot.setFixedHeight(180)
            plot.setLabel("left", unit)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.setLabel("bottom", "Elapsed time")
            plot.showGrid(x=True, y=True, alpha=0.15)
            plot.setMouseEnabled(x=True, y=True)
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.disableAutoRange()
            plot.setToolTip("Drag to pan; wheel to zoom. Use the left axis for Y only or the bottom axis for X only.")
            if key in ("cpu_percent", "gpu_percent"):
                plot.setYRange(0, 100, padding=0.03)
            self.plots[key] = plot
            plot.getViewBox().sigRangeChangedManually.connect(lambda axes, k=key: self.chart_range_changed(k, axes))
            self.curves[key] = plot.plot(pen=pg.mkPen("#79b8ff", width=2), connect="finite")
            self.curves[key].setDownsampling(auto=True, method="peak")
            self.curves[key].setClipToView(True)
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#9aaec6"))
            plot.addItem(line)
            self.lines[key] = line
            boundaries = []
            for title, color, position in [("Record", "#65df9b", 0.9), ("Stop", "#f4b45e", 0.75)]:
                boundary = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color, width=2),
                                           label=title, labelOpts={"position": position, "color": color})
                boundary.setZValue(20)
                boundary.hide()
                plot.addItem(boundary)
                boundaries.append(boundary)
            self.record_lines[key] = boundaries
            if key == "memory_mib":
                self.ram_limit = pg.InfiniteLine(
                    angle=0, movable=False, pen=pg.mkPen("#ff6b6b", width=2),
                    label="Physical RAM: {value:.2f} GiB",
                    labelOpts={"position": 0.98, "color": "#ff6b6b", "anchors": [(1, 0), (1, 1)]},
                )
                self.ram_limit.setZValue(10)
                plot.addItem(self.ram_limit)
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
        self.cursor.valueChanged.connect(self.cursor_changed)
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
            "memory_mib": "System memory · RAM + swap" if system else "Program memory · PSS RAM + swap",
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
        self.set_memory_range()

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

    @staticmethod
    def set_capacity_axis(plot, capacity):
        plot.setYRange(0, capacity, padding=0)
        step = 2 ** math.ceil(math.log2(capacity / 4))
        ticks = [index * step for index in range(math.ceil(capacity / step))
                 if index * step < capacity - step * 0.35] + [capacity]
        plot.getAxis("left").setTicks([[(value, f"{value:.2f}".rstrip("0").rstrip(".")) for value in ticks]])

    def set_vram_range(self):
        plot = self.plots["vram_mib"]
        capacity = self.vram_capacity_gib()
        if "vram_mib" in self.manual_y:
            plot.getAxis("left").setTicks(None)
        elif capacity is not None:
            self.set_capacity_axis(plot, capacity)
        else:
            plot.getAxis("left").setTicks(None)
            peak = maximum(self.samples, "vram_mib")
            plot.setYRange(0, max(1, peak["vram_mib"] / 1024 * 1.1 if peak else 1), padding=0)

    def set_memory_range(self):
        plot = self.plots["memory_mib"]
        ram, swap = self.ram_capacity_gib(), self.swap_capacity_gib()
        capacity = ram + swap if ram is not None and swap is not None else None
        self.ram_limit.setVisible(ram is not None)
        if ram is not None:
            self.ram_limit.setValue(ram)
        if "memory_mib" in self.manual_y:
            plot.getAxis("left").setTicks(None)
            return
        if capacity is not None:
            self.set_capacity_axis(plot, capacity)
        else:
            # Never substitute this computer's capacity for a legacy recording.
            plot.getAxis("left").setTicks(None)
            peak = maximum(self.samples, "memory_mib")
            plot.setYRange(0, max(ram or 1, peak["memory_mib"] / 1024 * 1.1 if peak else 1), padding=0)

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
        if self.range_mode.currentData() == -1:
            self.follow.setChecked(False)
        else:
            self.follow.setChecked(True)
        self.refresh()

    def follow_changed(self, checked):
        if checked:
            self.cursor_follows = True
            if self.range_mode.currentData() == -1:
                self.range_mode.blockSignals(True)
                self.range_mode.setCurrentIndex(0)
                self.range_mode.blockSignals(False)
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
        elif self.follow.isChecked():
            view = "LIVE · charts follow incoming data"
            if not self.cursor_follows:
                view += " · inspecting a selected sample"
        else:
            view = "FIXED TIME RANGE · monitoring and recording continue · click Back to live to resume scrolling"
        self.view_status.setText(f"{view} · {activity}")
        self.live_button.setEnabled(not self.viewing_live or not self.follow.isChecked() or not self.cursor_follows)
        if self.fullscreen_dialog:
            self.fullscreen_status.setText(f"{self.record_clock.text()} · {self.view_status.text()}")

    def update_record_markers(self):
        span = self.recording_span if self.viewing_live else self.metadata
        start = span.get("live_started_elapsed") if self.viewing_live else (0 if span else None)
        if self.viewing_live:
            stop = span.get("live_ended_elapsed")
        else:
            stop = span.get("duration")
            if stop is None and span.get("ended_at") is not None and span.get("started_at") is not None:
                stop = max(0, span["ended_at"] - span["started_at"])
        for boundaries in self.record_lines.values():
            for line, value in zip(boundaries, (start, stop)):
                line.setVisible(value is not None)
                if value is not None:
                    line.setValue(value)

    def set_chart_time_range(self, start, end):
        start = max(0, start)
        end = max(start + 0.2, end)
        self.follow.blockSignals(True)
        self.follow.setChecked(False)
        self.follow.blockSignals(False)
        self.cursor_follows = False
        self.range_mode.blockSignals(True)
        self.range_mode.setCurrentIndex(3)
        self.range_mode.blockSignals(False)
        for spin, value in [(self.start_time, start), (self.end_time, end)]:
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.refresh()

    def chart_range_changed(self, key, axes):
        if axes[1]:
            self.manual_y.add(key)
            if key in ("memory_mib", "vram_mib"):
                self.plots[key].getAxis("left").setTicks(None)
        if axes[0]:
            self.set_chart_time_range(*self.plots[key].viewRange()[0])

    def zoom_chart(self, key, axis, factor):
        plot = self.plots[key]
        low, high = plot.viewRange()[axis]
        center = (low + high) / 2
        if self.samples:
            sample = self.samples[min(self.cursor.value(), len(self.samples) - 1)]
            value = sample["elapsed"] if axis == 0 else metric_value(sample, key)
            if value is not None:
                if axis == 1 and key in ("memory_mib", "vram_mib"):
                    value /= 1024
                if low <= value <= high:
                    center = value
        width = max(0.2 if axis == 0 else 0.001, (high - low) * factor)
        start = max(0, center - width / 2)
        if axis == 0:
            self.set_chart_time_range(start, start + width)
        else:
            self.manual_y.add(key)
            plot.setYRange(start, start + width, padding=0)
            if key in ("memory_mib", "vram_mib"):
                plot.getAxis("left").setTicks(None)

    def fit_y(self, key):
        start, end = self.bounds()
        keys = (key, "write_mib_s") if key == "read_mib_s" else (key,)
        values = [metric_value(sample, metric) for sample in self.samples
                  if start - 0.05 <= sample["elapsed"] <= end + 0.05 for metric in keys]
        values = [value / 1024 if key in ("memory_mib", "vram_mib") else value for value in values
                  if isinstance(value, (int, float)) and math.isfinite(value)]
        if not values:
            return
        margin = max(0.05, (max(values) - min(values)) * 0.1, max(values) * 0.02)
        self.manual_y.add(key)
        self.plots[key].setYRange(max(0, min(values) - margin), max(values) + margin, padding=0)
        if key in ("memory_mib", "vram_mib"):
            self.plots[key].getAxis("left").setTicks(None)

    def reset_chart(self, key):
        self.manual_y.discard(key)
        self.cursor_follows = True
        self.range_mode.setCurrentIndex(0)
        self.follow.setChecked(True)
        self.refresh()

    def toggle_fullscreen(self, key):
        if self.fullscreen_dialog:
            self.fullscreen_dialog.close()
            return
        group, plot = self.chart_groups[key], self.plots[key]
        dialog = QDialog(self)
        dialog.setWindowTitle(group.title() + " · Chequebo")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        self.fullscreen_status = QLabel()
        self.fullscreen_status.setWordWrap(True)
        layout.addWidget(self.fullscreen_status)
        live = QPushButton("Back to live")
        live.clicked.connect(self.show_live)
        layout.addWidget(live)
        self.chart_grid.removeWidget(group)
        layout.addWidget(group, 1)
        plot.setMinimumHeight(180)
        plot.setMaximumHeight(16777215)
        self.chart_buttons[key]["fullscreen"].setText("↙ Restore")
        self.fullscreen_dialog = dialog
        dialog.finished.connect(lambda _: self.restore_chart(key, dialog))
        self.update_activity()
        dialog.showFullScreen()

    def restore_chart(self, key, dialog):
        group = self.chart_groups[key]
        dialog.layout().removeWidget(group)
        index = list(self.chart_groups).index(key)
        self.chart_grid.addWidget(group, index // 2, index % 2)
        self.plots[key].setFixedHeight(180)
        self.chart_buttons[key]["fullscreen"].setText("⛶ Full screen")
        group.show()
        self.fullscreen_dialog = None

    def refresh(self):
        self.set_memory_range()
        self.set_vram_range()
        self.update_record_markers()
        self.update_activity()
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
            if not self.viewing_live:
                last = max(last, self.metadata.get("duration", last))
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
            values = [metric_value(sample, key) for sample in selected]
            y = np.array([value if value is not None else np.nan for value in values])
            if key in ("memory_mib", "vram_mib"):
                y = y / 1024
            curve.setData(x, y)
        for key, plot in self.plots.items():
            plot.setXRange(start, max(end, start + 1), padding=0)
            if key not in self.manual_y and key in ("cpu_percent", "gpu_percent"):
                plot.setYRange(0, 100, padding=0.03)
            if key not in self.manual_y and key not in ("cpu_percent", "gpu_percent", "memory_mib", "vram_mib"):
                keys = (key, "write_mib_s") if key == "read_mib_s" else (key,)
                values = [sample[k] for sample in selected for k in keys
                          if isinstance(sample.get(k), (int, float)) and math.isfinite(sample[k])]
                plot.setYRange(0, max(1, max(values, default=0) * 1.1), padding=0)
        self.cursor.blockSignals(True)
        self.cursor.setRange(0, len(self.samples) - 1)
        if self.cursor_follows and self.follow.isChecked():
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
            value = metric_value(sample, key)
            display, unit = display_metric(key, value, dict((k,u) for k,_,u in self.metrics)[key])
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
        if not self.samples or event.button() != Qt.MouseButton.LeftButton or not plot.getViewBox().sceneBoundingRect().contains(event.scenePos()):
            return
        elapsed = plot.plotItem.vb.mapSceneToView(event.scenePos()).x()
        self.select_time(elapsed)

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
        for row, (key, _, unit) in enumerate(self.metrics):
            for base, peak in [(1, maximum(self.samples, key)),
                               (3, maximum(self.samples, key, start - 0.05, end + 0.05))]:
                display, display_unit = display_metric(key, peak[key] if peak else None, unit)
                value = f"{display:.2f} {display_unit}".strip() if peak else "Unavailable"
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
            self.manual_y.clear()
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
        event.accept()
