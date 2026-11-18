"""Continuous system monitoring with optional, independently controlled recording."""

import queue
import time
from pathlib import Path

import psutil
from PyQt6.QtCore import QThread, pyqtSignal as Signal

from .metrics import GPU, SystemSampler
from .phases import PhaseReceiver
from .session import SessionWriter


class SystemMonitor(QThread):
    sample_ready = Signal(dict)
    recording_changed = Signal(str, dict)
    failed = Signal(str)

    def __init__(self, interval=1.0, parent=None):
        super().__init__(parent)
        self.interval = interval
        self.commands = queue.Queue()
        self.store = None
        self.path = None
        self.metadata = {}

    def start_recording(self, path):
        self.commands.put(("record", (Path(path), time.monotonic(), time.time())))

    def stop_recording(self):
        self.commands.put(("save", None))

    def set_interval(self, seconds):
        self.commands.put(("interval", seconds))

    def retry_gpu(self):
        self.commands.put(("gpu", None))

    def stop(self):
        self.commands.put(("quit", None))

    def _finish(self, error=""):
        if self.store is None:
            return
        self.metadata.update(state="error" if error else "stopped", error=error,
                             ended_at=time.time(), duration=max(0, time.monotonic() - self.record_started))
        self.metadata["live_ended_elapsed"] = self.metadata["live_started_elapsed"] + self.metadata["duration"]
        try:
            self.store.set_meta(self.metadata)
        except Exception as exc:
            self.metadata.update(state="error", error=f"Unable to finalize recording: {exc}")
        finally:
            self.store.close()
            self.store = None
        self.recording_changed.emit(str(self.path), dict(self.metadata))

    def run(self):
        sampler = None
        phases = None
        try:
            sampler = SystemSampler()
            phases = PhaseReceiver()
            next_sample = time.monotonic()
            while True:
                try:
                    action, value = self.commands.get(timeout=max(0, next_sample - time.monotonic()))
                except queue.Empty:
                    action, value = "sample", None
                if action == "quit":
                    break
                if action == "record" and self.store is None:
                    path, self.record_started, started_at = value
                    metadata = {
                        "scope": "system", "command": "Whole computer", "interval": self.interval,
                        "cpu_count": psutil.cpu_count() or 1,
                        "physical_cores": psutil.cpu_count(logical=False),
                        "ram_total_mib": psutil.virtual_memory().total / 1024**2,
                        "swap_total_mib": psutil.swap_memory().total / 1024**2,
                        "started_at": started_at, "state": "recording",
                        "live_started_elapsed": max(0, self.record_started - sampler.started),
                    }
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.touch(exist_ok=False)  # Never replace an existing recording.
                        self.store = SessionWriter(path, metadata)
                    except Exception as exc:
                        self.recording_changed.emit(str(path), {**metadata, "state": "error", "error": str(exc)})
                    else:
                        self.path, self.metadata = path, metadata
                        self.first_record_sample = True
                        self.recording_changed.emit(str(path), {**metadata, "started_monotonic": self.record_started})
                        next_sample = time.monotonic()
                elif action == "save":
                    self._finish()
                elif action == "interval":
                    self.interval = value
                    next_sample = time.monotonic()
                elif action == "gpu":
                    sampler.gpu.close()
                    sampler.gpu = GPU()
                elif action == "sample":
                    tick = time.monotonic()
                    sample = sampler.sample()
                    sample['phases'] = phases.sample(sample.get('process_snapshot', []), sample['timestamp'])
                    sample['phase_note'] = phases.note
                    if self.store is not None:
                        recorded = {**sample, "elapsed": max(0, tick - self.record_started)}
                        recorded['phases'] = [event for event in sample['phases']
                                              if event.get('end') is None or event['end'] >= self.metadata['started_at']]
                        if self.first_record_sample:
                            # Rate intervals must not extend to before Record was pressed.
                            for key in ("cpu_percent", "cpu_equivalent", "read_mib_s", "write_mib_s", "net_recv_mbps", "net_sent_mbps"):
                                recorded[key] = None
                            recorded["cpu_system"] = []
                            recorded['process_snapshot'] = [
                                {**row, 'cpu_percent': None, 'cpu_equivalent': None, 'ram_delta_mib': None}
                                for row in sample.get('process_snapshot', [])]
                            self.first_record_sample = False
                        try:
                            self.store.append(recorded)
                        except Exception as exc:
                            self._finish(f"Unable to save sample: {exc}")
                    self.sample_ready.emit(sample)
                    next_sample = max(tick + self.interval, time.monotonic())
        except Exception as exc:
            error = f"Monitoring stopped: {type(exc).__name__}: {exc}"
            self._finish(error)
            self.failed.emit(error)
        finally:
            self._finish()
            if sampler:
                sampler.close()
            if phases:
                phases.close()
