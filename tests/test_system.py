import time
from types import SimpleNamespace as NS

import pytest

from chequebo import metrics
from chequebo.monitor import SystemMonitor
from chequebo.session import SessionWriter, export_maxima, load_session
from chequebo.ui import Window


class FakeGPU:
    note = "Test GPU"

    def sample(self):
        return 42, 1024

    def close(self):
        pass


class FakeSampler:
    def __init__(self):
        self.started = time.monotonic()
        self.gpu = FakeGPU()

    def sample(self):
        return dict(elapsed=time.monotonic() - self.started, timestamp=time.time(),
                    ram_mib=128, cpu_percent=50, cpu_equivalent=4, cpu_system=[50] * 8,
                    read_mib_s=2, write_mib_s=3, swap_mib=0, gpu_percent=42,
                    vram_mib=1024, processes=120, gpu_note="Test GPU")

    def close(self):
        pass


def until(predicate, app, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


@pytest.mark.parametrize("count", [1, 4, 8, 16, 64, 128])
def test_system_measurements_use_machine_capacity(monkeypatch, count):
    monkeypatch.setattr(metrics, "GPU", FakeGPU)
    monkeypatch.setattr(metrics.psutil, "cpu_percent", lambda **_: [50] * count)
    monkeypatch.setattr(metrics.psutil, "virtual_memory", lambda: NS(total=8192*metrics.MIB, available=3072*metrics.MIB))
    monkeypatch.setattr(metrics.psutil, "swap_memory", lambda: NS(used=64*metrics.MIB))
    io = NS(read_bytes=100*metrics.MIB, write_bytes=200*metrics.MIB)
    monkeypatch.setattr(metrics.psutil, "disk_io_counters", lambda: io)
    sampler = metrics.SystemSampler()
    assert sampler.sample()["cpu_percent"] is None
    monkeypatch.setattr(metrics.time, "monotonic", lambda: sampler.previous + 2)
    io = NS(read_bytes=104*metrics.MIB, write_bytes=206*metrics.MIB)
    sample = sampler.sample()
    assert sample["ram_mib"] == 5120
    assert sample["swap_mib"] == 64
    assert sample["cpu_percent"] == 50
    assert sample["cpu_equivalent"] == count / 2
    assert len(sample["cpu_system"]) == count
    # Subtracting floating-point clock values can introduce tiny rounding errors.
    assert sample["read_mib_s"] == pytest.approx(2, rel=1e-12)
    assert sample["write_mib_s"] == pytest.approx(3, rel=1e-12)


def test_monitor_records_only_on_request_and_keeps_running_after_stop(tmp_path, app, monkeypatch):
    monkeypatch.setattr("chequebo.monitor.SystemSampler", FakeSampler)
    monitor = SystemMonitor(.03)
    live, changes = [], []
    monitor.sample_ready.connect(live.append)
    monitor.recording_changed.connect(lambda path, meta: changes.append((path, meta)))
    monitor.start()
    try:
        until(lambda: len(live) >= 3, app)
        assert not list(tmp_path.iterdir())
        first = tmp_path / "first.chequebo"
        monitor.start_recording(first)
        until(lambda: len(changes) == 1 and len(live) >= 7, app)
        monitor.stop_recording()
        until(lambda: len(changes) == 2, app)
        metadata, samples = load_session(first)
        assert metadata["scope"] == "system" and metadata["state"] == "stopped"
        assert samples[0]["elapsed"] < .1
        assert samples[0]["cpu_percent"] is None
        assert samples[-1]["cpu_percent"] == 50
        assert samples[0]["timestamp"] >= metadata["started_at"]
        count = len(live)
        until(lambda: len(live) >= count + 3, app)
        assert monitor.isRunning()
        assert len(load_session(first)[1]) == len(samples)
        second = tmp_path / "second.chequebo"
        monitor.start_recording(second)
        until(lambda: len(changes) == 3 and len(live) >= count + 6, app)
        monitor.stop()
        assert monitor.wait(3000)
        metadata2, samples2 = load_session(second)
        assert metadata2["state"] == "stopped"
        assert samples2[0]["elapsed"] < .1
        assert samples2[0]["timestamp"] >= metadata2["started_at"]
    finally:
        monitor.stop()
        assert monitor.wait(3000)


def test_existing_file_and_storage_failure_do_not_stop_monitor(tmp_path, app, monkeypatch):
    monkeypatch.setattr("chequebo.monitor.SystemSampler", FakeSampler)
    path = tmp_path / "existing.chequebo"
    path.write_bytes(b"preserve this file")
    monitor = SystemMonitor(.03)
    live, changes = [], []
    monitor.sample_ready.connect(live.append)
    monitor.recording_changed.connect(lambda path, meta: changes.append(meta))
    monitor.start()
    try:
        monitor.start_recording(path)
        until(lambda: len(changes) == 1, app)
        assert changes[-1]["state"] == "error"
        assert path.read_bytes() == b"preserve this file"
        def fail_append(*_):
            raise OSError("Disk full")
        monkeypatch.setattr(SessionWriter, "append", fail_append)
        monitor.start_recording(tmp_path / "full.chequebo")
        until(lambda: len(changes) == 3, app)
        assert changes[-1]["state"] == "error" and "Disk full" in changes[-1]["error"]
        count = len(live)
        until(lambda: len(live) >= count + 2, app)
        assert monitor.isRunning()
    finally:
        monitor.stop()
        assert monitor.wait(3000)


def test_gui_starts_live_and_separates_saved_view_from_bounded_history(tmp_path, app, monkeypatch):
    monkeypatch.setattr("chequebo.monitor.SystemSampler", FakeSampler)
    window = Window(tmp_path)
    window.interval.setValue(.2)
    window.show()
    try:
        until(lambda: len(window.samples) >= 2, app)
        assert not list(tmp_path.glob("*.chequebo"))
        assert window.chart_groups["ram_mib"].title().startswith("System RAM")
        window.start_recording()
        until(lambda: window.recording, app)
        until(lambda: len(window.samples) >= 4, app)
        window.stop_recording()
        until(lambda: window.last_recording is not None, app)
        window.load_recording(window.last_recording)
        opened = window.samples
        size = len(opened)
        assert not window.viewing_live
        assert "Recording peaks" in window.peaks_group.title()
        # Simulate a long uptime without rendering thousands of frames.
        for index in range(4000):
            window.add_sample({**FakeSampler().sample(), "elapsed": 2 + index * .2})
        assert len(window.live_samples) <= 3001
        assert window.live_samples[-1]["elapsed"] - window.live_samples[0]["elapsed"] <= 600
        assert window.samples is opened and len(opened) == size
        window.show_live()
        assert window.samples is window.live_samples
        assert window.monitor.isRunning()
        assert window.cursor.value() == len(window.samples) - 1
        assert "Live buffer peaks" in window.peaks_group.title()
        old = tmp_path / "legacy.chequebo"
        writer = SessionWriter(old, {"cpu_count": 8})
        writer.append(FakeSampler().sample())
        writer.close()
        window.load_recording(old)
        assert "PSS" in window.chart_groups["ram_mib"].title()
        assert "Program VRAM" in window.table.item(6, 0).text()
    finally:
        window.close()
        assert not window.monitor.isRunning()


def test_system_peak_export_has_system_labels(tmp_path):
    path = tmp_path / "peaks.csv"
    export_maxima(path, [FakeSampler().sample()], 0, 1, {"scope": "system"})
    text = path.read_text()
    assert "System RAM" in text and "Program RAM" not in text
