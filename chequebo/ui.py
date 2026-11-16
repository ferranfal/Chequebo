"""Ventana Qt. El muestreo y la persistencia viven fuera del hilo de la interfaz."""

import bisect
import math
import os
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import psutil
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .metrics import GPU
from .recorder import Recorder, launch
from .session import METRICS, clock, export_maxima, export_samples, load_session, maximum


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [clock(value) + (f".{int(value * 10) % 10}" if spacing < 1 else "") for value in values]


class ProcessDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Elegir un proceso · Chequebo")
        self.resize(700, 450)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText="Filtrar por nombre o PID")
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["PID", "Programa", "Comando"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        for process in psutil.process_iter(["pid", "name", "cmdline", "uids", "create_time"], ad_value=None):
            info = process.info
            if not info["uids"] or info["uids"].real != os.getuid() or info["pid"] == os.getpid():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate([str(info["pid"]), info["name"] or "?",
                                          " ".join(info["cmdline"] or [])]):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, info["create_time"])
                self.table.setItem(row, col, item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.doubleClicked.connect(self.accept)
        self.search.textChanged.connect(self.filter)

    def filter(self, text):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, not any(
                text.casefold() in self.table.item(row, col).text().casefold() for col in range(3)
            ))

    def selected(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.text()), item.data(Qt.ItemDataRole.UserRole)


class Window(QMainWindow):
    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.samples, self.metadata = [], {}
        self.recorder = None
        self.session_path = None
        self.plots, self.curves, self.lines, self.value_labels = {}, {}, {}, {}
        self.core_bars = []
        self.setWindowTitle("Chequebo · Monitor de procesos para Linux")
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
        self.status = QLabel("Elige un comando o un proceso. Todas las mediciones se guardan localmente.")
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        self._core_layout(psutil.cpu_count() or 1)

    def _build_launch(self):
        group = QGroupBox("Chequebo · Nueva sesión")
        grid = QGridLayout(group)
        self.command = QLineEdit(placeholderText="Comando y argumentos, por ejemplo: python3 mi_programa.py")
        self.cwd = QLineEdit(str(Path.cwd()))
        self.cwd.setToolTip("Carpeta de trabajo del programa")
        browse = QPushButton("Carpeta…")
        browse.clicked.connect(self.choose_cwd)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 60)
        self.interval.setValue(1)
        self.interval.setSuffix(" s / muestra")
        self.interval.setToolTip("Un intervalo menor captura más detalle y consume más recursos.")
        grid.addWidget(self.command, 0, 0, 1, 3)
        grid.addWidget(self.cwd, 1, 0)
        grid.addWidget(browse, 1, 1)
        grid.addWidget(self.interval, 1, 2)
        controls = QHBoxLayout()
        self.start_button = QPushButton("▶ Lanzar y grabar")
        self.attach_button = QPushButton("● Elegir proceso y grabar")
        self.stop_button = QPushButton("■ Parar grabación")
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Solo detiene el registro; el programa continúa ejecutándose.")
        self.open_button = QPushButton("Abrir sesión…")
        gpu = QPushButton("Comprobar GPU")
        self.start_button.clicked.connect(self.start_command)
        self.attach_button.clicked.connect(self.attach_dialog)
        self.stop_button.clicked.connect(lambda: self.recorder.stop() if self.recorder else None)
        self.open_button.clicked.connect(self.open_session)
        gpu.clicked.connect(self.check_gpu)
        for button in [self.start_button, self.attach_button, self.stop_button, self.open_button, gpu]:
            controls.addWidget(button)
        grid.addLayout(controls, 2, 0, 1, 3)
        self.machine = QLabel()
        grid.addWidget(self.machine, 3, 0, 1, 3)
        self.layout.addWidget(group)

    def _build_time_controls(self):
        group = QGroupBox("Intervalo de las gráficas")
        row = QHBoxLayout(group)
        self.follow = QCheckBox("Seguir grabación")
        self.follow.setChecked(True)
        self.range_mode = QComboBox()
        for label, seconds in [("Toda la sesión", 0), ("Últimos 2 minutos", 120), ("Últimos 5 minutos", 300)]:
            self.range_mode.addItem(label, seconds)
        self.range_mode.addItem("Intervalo personalizado", -1)
        self.start_time, self.end_time = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in [self.start_time, self.end_time]:
            spin.setRange(0, 100000000)
            spin.setDecimals(1)
            spin.setSuffix(" s")
        self.start_time.setToolTip("Segundos desde el inicio")
        self.end_time.setToolTip("Segundos desde el inicio")
        for widget in [self.follow, self.range_mode, QLabel("Desde"), self.start_time, QLabel("Hasta"), self.end_time]:
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
            ("ram_mib", "RAM del programa + hijos · memoria PSS", "MiB"),
            ("cpu_percent", "CPU del programa · 100 % = todo el equipo", "%"),
            ("gpu_percent", "GPU · equipo completo (media entre tarjetas)", "%"),
            ("vram_mib", "VRAM del programa + hijos · NVIDIA", "MiB"),
            ("read_mib_s", "Disco del programa · lectura / escritura", "MiB/s"),
        ]
        for index, (key, title, unit) in enumerate(chart_specs):
            group = QGroupBox(title)
            box = QVBoxLayout(group)
            label = QLabel("Sin muestras")
            label.setWordWrap(True)
            self.value_labels[key] = label
            box.addWidget(label)
            plot = pg.PlotWidget(axisItems={"bottom": TimeAxis(orientation="bottom")})
            plot.setFixedHeight(180)
            plot.setLabel("left", unit)
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.setLabel("bottom", "Tiempo desde el inicio")
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
        self.cores_group = QGroupBox("Cada CPU lógica · equipo completo · instante seleccionado")
        cores_box = QVBoxLayout(self.cores_group)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(200)
        area.setMaximumHeight(270)
        self.core_widget = QWidget()
        self.core_grid = QGridLayout(self.core_widget)
        area.setWidget(self.core_widget)
        cores_box.addWidget(area)
        self.core_time = QLabel("Selecciona un instante para ver el reparto de CPU.")
        self.core_time.setWordWrap(True)
        cores_box.addWidget(self.core_time)
        grid.addWidget(self.cores_group, 2, 1)
        cursor_row = QHBoxLayout()
        self.cursor = QSlider(Qt.Orientation.Horizontal)
        self.cursor.setRange(0, 0)
        self.cursor.valueChanged.connect(self.update_cursor)
        self.cursor_label = QLabel("Instante: —")
        cursor_row.addWidget(self.cursor_label)
        cursor_row.addWidget(self.cursor, 1)
        self.layout.addLayout(cursor_row)

    def _build_maxima(self):
        group = QGroupBox("Máximos registrados · programa + procesos hijos, excepto GPU global")
        box = QVBoxLayout(group)
        actions = QHBoxLayout()
        maxima_button = QPushButton("Exportar informe de máximos…")
        data_button = QPushButton("Exportar muestras CSV…")
        maxima_button.clicked.connect(lambda: self.export(True))
        data_button.clicked.connect(lambda: self.export(False))
        actions.addWidget(maxima_button)
        actions.addWidget(data_button)
        actions.addStretch()
        box.addLayout(actions)
        self.table = QTableWidget(len(METRICS), 5)
        self.table.setHorizontalHeaderLabels(["Recurso", "Máximo sesión", "Instante", "Máximo intervalo", "Instante"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for row, (_, title, _) in enumerate(METRICS):
            for col in range(5):
                self.table.setItem(row, col, QTableWidgetItem(title if col == 0 else "—"))
        self.table.setMinimumHeight(320)
        self.table.cellClicked.connect(self.jump_to_peak)
        box.addWidget(self.table)
        note = QLabel("Pulsa un máximo para inspeccionarlo. Son máximos muestreados: pueden quedar picos entre muestras. Los máximos de cada recurso pueden ocurrir en instantes distintos.")
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
        self.machine.setText(f"Equipo de esta sesión: {physical or '?'} núcleos físicos · {count} CPU lógicas. CPU del programa: porcentaje del total de CPU lógicas.")

    def choose_cwd(self):
        directory = QFileDialog.getExistingDirectory(self, "Carpeta de trabajo", self.cwd.text())
        if directory:
            self.cwd.setText(directory)

    def new_path(self):
        return self.data_dir / (time.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6] + ".chequebo")

    def start_command(self):
        if self.recorder and self.recorder.isRunning():
            return
        path = self.new_path()
        try:
            process, gate = launch(self.command.text(), self.cwd.text(), path.with_suffix(".log"))
            self.begin(process.pid, path, self.command.text(), process, gate)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "No se puede iniciar", str(exc))

    def attach_dialog(self):
        dialog = ProcessDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected():
            pid, created = dialog.selected()
            try:
                process = psutil.Process(pid)
                if process.create_time() != created:
                    raise ValueError("El proceso elegido ha terminado. Elige otro.")
                self.begin(pid, self.new_path(), " ".join(process.cmdline()) or process.name())
            except (psutil.Error, ValueError) as exc:
                QMessageBox.warning(self, "No se puede adjuntar", str(exc))

    def begin(self, pid, path, command, process=None, gate=None):
        self.samples = []
        self.metadata = {"command": command, "cpu_count": psutil.cpu_count() or 1,
                         "physical_cores": psutil.cpu_count(logical=False)}
        self.session_path = path
        self._core_layout(self.metadata["cpu_count"])
        self.follow.setChecked(True)
        self.range_mode.setCurrentIndex(0)
        self.recorder = Recorder(pid, path, self.interval.value(), command, process, gate, self)
        self.recorder.sample_ready.connect(self.add_sample)
        self.recorder.completed.connect(self.recording_completed)
        self.recorder.finished.connect(self.recording_thread_finished)
        self.recording_enabled(True)
        self.refresh()
        self.setWindowTitle(f"Chequebo · grabando · {command}")
        self.recorder.start()

    def recording_enabled(self, active):
        for widget in [self.start_button, self.attach_button, self.open_button, self.interval]:
            widget.setEnabled(not active)
        self.stop_button.setEnabled(active)

    def add_sample(self, sample):
        self.samples.append(sample)
        self.refresh()
        notes = " · ".join(sample["notes"])
        self.status.setText(f"● Grabando · {sample['processes']} procesos · {sample['threads']} hilos · {self.session_path}\n{sample['gpu_note']}" + (f"\n{notes}" if notes else ""))

    def recording_completed(self, path, metadata):
        self.metadata.update(metadata)
        state = {"finished": "Programa e hijos observados finalizados", "stopped": "Grabación detenida; el programa puede continuar", "error": "Error de grabación"}[metadata["state"]]
        exit_info = f" · código de salida {metadata['exit_code']}" if metadata.get("exit_code") is not None else ""
        empty = " · sin muestras: ejecución demasiado breve" if not self.samples else ""
        self.status.setText(f"{state}{exit_info}{empty}\nSesión: {path}" + ("\n" + metadata["error"] if metadata.get("error") else ""))
        self.setWindowTitle("Chequebo · sesión guardada")

    def recording_thread_finished(self):
        self.recording_enabled(False)

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
                label.setText("Sin muestras")
            self.cursor.setRange(0, 0)
            self.cursor_label.setText("Instante: —")
            for bar in self.core_bars:
                bar.setValue(0)
                bar.setFormat("—")
            for row in range(len(METRICS)):
                for col in range(1, 5):
                    self.table.item(row, col).setText("—")
                    self.table.item(row, col).setData(Qt.ItemDataRole.UserRole, None)
            return
        if self.follow.isChecked():
            last = self.samples[-1]["elapsed"]
            window = self.range_mode.currentData()
            start = max(0, last - window) if window and window > 0 else 0
            for spin, value in [(self.start_time, start), (self.end_time, last)]:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        start, end = self.bounds()
        # Margen por la precisión decimal del selector, sin descartar la última muestra.
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
        old_cursor = self.cursor.value()
        at_end = old_cursor == self.cursor.maximum()
        self.cursor.blockSignals(True)
        self.cursor.setRange(0, len(self.samples) - 1)
        if self.follow.isChecked() and at_end:
            self.cursor.setValue(len(self.samples) - 1)
        self.cursor.blockSignals(False)
        self.update_cursor(self.cursor.value())
        self.update_maxima()

    def update_cursor(self, index):
        if not self.samples:
            return
        sample = self.samples[min(index, len(self.samples) - 1)]
        self.cursor_label.setText("Instante: " + clock(sample["elapsed"]))
        for key, line in self.lines.items():
            line.setPos(sample["elapsed"])
            value = sample.get(key)
            label = "No disponible" if value is None else f"{value:.2f} {dict((k,u) for k,_,u in METRICS)[key]}"
            if key == "cpu_percent" and value is not None:
                label += f" del equipo · {sample['cpu_equivalent']:.2f} CPU lógicas equivalentes"
            if key == "read_mib_s":
                write = sample.get("write_mib_s")
                label = f"Lectura: {label} · escritura: " + (f"{write:.2f} MiB/s" if write is not None else "no disponible")
            self.value_labels[key].setText(label)
        cpus = sample.get("cpu_system", [])
        if cpus and len(cpus) != len(self.core_bars):
            self._core_layout(len(cpus))
        for i, bar in enumerate(self.core_bars):
            available = i < len(cpus)
            bar.setValue(round(cpus[i]) if available else 0)
            bar.setFormat("%p%" if available else "—")
        self.core_time.setText(f"{clock(sample['elapsed'])} · actividad de todo el equipo, incluido el resto de programas. Cada barra va de 0 a 100 %.")

    def chart_clicked(self, plot, event):
        if not self.samples or not plot.sceneBoundingRect().contains(event.scenePos()):
            return
        elapsed = plot.plotItem.vb.mapSceneToView(event.scenePos()).x()
        self.select_time(elapsed)

    def select_time(self, elapsed):
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
        for row, (key, _, unit) in enumerate(METRICS):
            for base, peak in [(1, maximum(self.samples, key)),
                               (3, maximum(self.samples, key, start - 0.05, end + 0.05))]:
                value = f"{peak[key]:.2f} {unit}".strip() if peak else "No disponible"
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
        path, _ = QFileDialog.getOpenFileName(self, "Abrir sesión", str(self.data_dir), "Sesiones Chequebo (*.chequebo)")
        if path:
            try:
                self.metadata, self.samples = load_session(path)
                self.session_path = Path(path)
                self._core_layout(self.metadata["cpu_count"])
                self.range_mode.setCurrentIndex(0)
                self.follow.setChecked(True)
                self.refresh()
                state = self.metadata.get("state", "unknown")
                warning = " · grabación interrumpida; se recuperan las muestras guardadas" if state == "recording" else ""
                self.status.setText(f"Sesión abierta{warning}: {path}\n{self.metadata.get('command', '')}")
            except (OSError, ValueError, KeyError) as exc:
                QMessageBox.warning(self, "No se puede abrir", str(exc))

    def export(self, maxima):
        if not self.samples:
            QMessageBox.information(self, "Sin datos", "Primero graba o abre una sesión.")
            return
        default = self.data_dir / ("chequebo-maximos.csv" if maxima else "chequebo-muestras.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Exportar CSV", str(default), "CSV (*.csv)")
        if path:
            try:
                if maxima:
                    start, end = self.bounds()
                    export_maxima(path, self.samples, start - 0.05, end + 0.05)
                else:
                    export_samples(path, self.samples)
            except OSError as exc:
                QMessageBox.warning(self, "Error al exportar", str(exc))

    def check_gpu(self):
        gpu = GPU()
        try:
            usage, _ = gpu.sample(set())
            details = [gpu.note]
            if gpu.nv:
                for handle in gpu.handles:
                    name = gpu.nv.nvmlDeviceGetName(handle)
                    memory = gpu.nv.nvmlDeviceGetMemoryInfo(handle)
                    details.append(f"{name} · {memory.total / 1024**3:.1f} GiB VRAM")
            details.append(f"Actividad global: {usage:.0f} %" if usage is not None else "No se puede leer la actividad de la GPU.")
            details.append("La actividad corresponde a toda la tarjeta. La VRAM se atribuye a los PID observados cuando el controlador lo permite.")
            if self.recorder and self.recorder.isRunning():
                self.recorder.retry_gpu.set()
            QMessageBox.information(self, "Diagnóstico de GPU · Chequebo", "\n\n".join(details))
        except Exception as exc:
            QMessageBox.warning(self, "Diagnóstico de GPU", str(exc))
        finally:
            gpu.close()

    def closeEvent(self, event):
        if self.recorder and self.recorder.isRunning():
            self.recorder.stop()
            if not self.recorder.wait(5000):
                self.status.setText("Terminando de guardar la sesión; vuelve a cerrar en unos segundos.")
                event.ignore()
                return
        event.accept()
