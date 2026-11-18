"""Read-only process inspection at the selected recorded sample."""

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView,
                            QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                            QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .session import clock


class ProcessInspector(QWidget):
    follow_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sample = None
        self.selected_identity = None
        self.rows = []
        box = QVBoxLayout(self)
        self.heading = QLabel('Waiting for process samples')
        self.heading.setWordWrap(True)
        box.addWidget(self.heading)
        controls = QHBoxLayout()
        self.sort = QComboBox()
        for title, key in [('Most RAM', 'rss_mib'), ('Most CPU', 'cpu_percent'),
                           ('RAM growth', 'ram_delta_mib'), ('Most VRAM', 'vram_mib')]:
            self.sort.addItem(title, key)
        self.latest = QPushButton('Follow latest')
        self.latest.clicked.connect(self.follow_requested)
        controls.addWidget(self.sort)
        controls.addWidget(self.latest)
        box.addLayout(controls)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Filter PID, program or command…')
        box.addWidget(self.filter)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['PID', 'Program / script', 'RAM GiB', 'CPU %', 'Δ RAM GiB', 'VRAM GiB'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        # Qt 6.4 otherwise remeasures hundreds of rows after each cell change,
        # blocking the event loop long enough to starve the charts.
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in ((0,65), (2,75), (3,65), (4,90), (5,85)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(180)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        box.addWidget(self.table, 3)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(170)
        self.phases = QPlainTextEdit()
        self.phases.setReadOnly(True)
        self.peak_details = QPlainTextEdit()
        self.peak_details.setReadOnly(True)
        self.peak_details.setPlainText('Click a red peak band, peak button or peak-table value to inspect its recorded context.')
        tabs = self.tabs = QTabWidget()
        tabs.addTab(self.details, 'Process details')
        tabs.addTab(self.phases, 'Python phases')
        tabs.addTab(self.peak_details, 'Peak details')
        box.addWidget(tabs, 2)
        note = QLabel('Click a chart or peak to inspect that instant. RAM is resident memory (RSS); shared pages can appear in several processes. CPU % uses total machine capacity. Δ RAM compares consecutive samples. NVIDIA VRAM is reported allocation, not GPU activity per process.')
        note.setWordWrap(True)
        box.addWidget(note)
        export = QPushButton('Export process history to CSV…')
        export.clicked.connect(self.export_requested)
        box.addWidget(export)
        help_button = QPushButton('How to mark Python phases…')
        help_button.clicked.connect(self.phase_help)
        box.addWidget(help_button)
        self.sort.currentIndexChanged.connect(self.rebuild)
        self.filter.textChanged.connect(self.rebuild)

    def inspect(self, sample, following=False, live=False):
        changed = sample is not self.sample
        self.sample = sample
        available = sample.get('process_snapshot')
        mode = 'LIVE' if live and following else 'Selected sample'
        self.heading.setText(f"{mode} · {clock(sample['elapsed'])} ({sample['elapsed']:.2f} s)\n" +
                             (f'{len(available)} accessible processes' if available is not None else
                              'Process details were not saved in this recording.'))
        self.latest.setEnabled(not following)
        if changed:
            self.peak_details.setPlainText('Click a red peak band, peak button or peak-table value to inspect its recorded context.')
            self.rebuild()
            self.show_phases()

    def show_peak(self, peak):
        self.peak_details.setPlainText(peak['label'] + '\n\n' + peak['context']['details'] +
                                      '\n\nEqual maxima use their first sampled occurrence. A band marks an instant, not a measured duration.')
        self.tabs.setCurrentWidget(self.peak_details)

    def show_phases(self):
        events = self.sample.get('phases', [])
        lines = []
        for event in sorted(events, key=lambda e: e['start']):
            duration = (event['end'] if event.get('end') is not None else self.sample.get('timestamp', event['start'])) - event['start']
            lines.append(f"PID {event['pid']} · thread {event['thread']} · {event['name']}\n"
                         f"{event['state']} · {max(0, duration):.3f} s · started {datetime.fromtimestamp(event['start']).isoformat(sep=' ', timespec='milliseconds')}\n")
        self.phases.setPlainText('\n'.join(lines) if lines else
                                (self.sample.get('phase_note') or 'No Python phase markers at this instant. Use “How to mark Python phases” to label blocks in your algorithm. Existing recordings cannot recover unrecorded function calls.'))

    def phase_help(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Python phase markers')
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dialog.setText('For the Ubuntu package, add this to your Python program:\n\n'
                       'import sys\nsys.path.insert(0, "/usr/lib/chequebo")\nfrom chequebo.trace import phase\n\n'
                       'with phase("hstack"):\n    result = numpy.hstack(parts)\n\n'
                       'Keep Chequebo open, press Record, then run your algorithm normally. '
                       'Mark coarse steps such as loading, transforming or training. Nested phases and threads are supported. '
                       'The helper uses only the Python standard library and does not write files. '
                       'Markers identify code activity, not which allocation caused retained memory. '
                       'Finished phases appear in the sample interval containing their completion; running phases appear while active. '
                       'For source installs, import chequebo.trace from the installed environment or project path.')
        dialog.exec()

    def sort_resource(self, key):
        index = self.sort.findData(key)
        if index >= 0:
            self.sort.setCurrentIndex(index)

    def rebuild(self):
        if self.sample is None:
            return
        query = self.filter.text().casefold().strip()
        key = self.sort.currentData()
        rows = [row for row in self.sample.get('process_snapshot', [])
                if query in (' '.join(str(row['info'].get(k) or '') for k in ('pid', 'name', 'command'))).casefold()]
        self.rows = sorted(rows, key=lambda row: row.get(key) if row.get(key) is not None else -float('inf'), reverse=True)
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        selected = None
        for index, row in enumerate(self.rows):
            info = row['info']
            values = [str(info['pid']), info.get('command') or info.get('name') or 'Unknown']
            for metric in ('rss_mib', 'cpu_percent', 'ram_delta_mib', 'vram_mib'):
                value = row.get(metric)
                values.append('—' if value is None else f"{value if metric == 'cpu_percent' else value/1024:.3f}")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, col, item)
            if row['identity'] == self.selected_identity:
                selected = index
        if selected is not None:
            self.table.selectRow(selected)
        elif self.rows and self.selected_identity is None:
            self.table.selectRow(0)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        self.selection_changed()

    def selection_changed(self):
        index = self.table.currentRow()
        if not self.table.selectedItems() or not 0 <= index < len(self.rows):
            self.details.setPlainText('Select a process to see its command, parent and recorded activity.' if self.rows else
                                      'No matching process data at this instant. Older recordings cannot reconstruct past processes.')
            return
        row = self.rows[index]
        self.selected_identity = row['identity']
        info = row['info']
        by_pid = {p['info']['pid']: p['info'] for p in self.sample.get('process_snapshot', [])}
        ancestry, seen = [], {info['pid']}
        ppid = info.get('ppid')
        while ppid and ppid not in seen:
            seen.add(ppid)
            parent = by_pid.get(ppid)
            ancestry.append(f"PID {ppid}: {(parent.get('command') or parent.get('name')) if parent else 'not captured'}")
            ppid = parent.get('ppid') if parent else None
        equivalent = row.get('cpu_equivalent')
        text = (
            f"{info.get('name') or 'Unknown'} · PID {info['pid']} · user {info.get('user') or 'unavailable'}\n"
            f"Started: {datetime.fromtimestamp(info['created']).isoformat(sep=' ', timespec='seconds')}\n"
            f"Command: {info.get('command') or 'Unavailable (kernel task or permissions)'}\n"
            f"Status: {row.get('status') or 'unknown'} · threads: {row.get('threads') or '—'}\n")
        text += f'CPU equivalents: {equivalent:.2f}\n' if equivalent is not None else 'CPU equivalents: unavailable (baseline or permissions)\n'
        self.details.setPlainText(text)
        self.details.appendPlainText('Launched by (current parent chain):\n' + ('\n'.join(ancestry) or 'No parent captured'))
        self.details.appendPlainText('\nOpen the Python phases tab to see recorded steps. Function activity is not inferred from process names.')
