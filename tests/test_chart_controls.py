import time

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from chequebo.session import SessionWriter, load_session
from chequebo.ui import Window
from test_system import FakeSampler, until


def sample(elapsed):
    return {**FakeSampler().sample(), "elapsed": elapsed, "ram_mib": 10*1024,
            "ram_total_mib": 64*1024, "swap_total_mib": 100*1024,
            "vram_mib": 2*1024, "vram_total_mib": 8*1024}


def test_selecting_a_sample_does_not_freeze_live_charts(tmp_path, app):
    window = Window(tmp_path, auto_start=False)
    try:
        for t in range(1, 4):
            window.add_sample(sample(t))
        window.select_time(1)
        window.add_sample(sample(4))
        assert window.follow.isChecked()
        assert window.cursor.value() == 0
        assert window.bounds()[1] == 4
        assert window.curves["memory_mib"].getData()[0][-1] == 4
        window.cursor.setValue(1)
        window.add_sample(sample(5))
        assert window.bounds()[1] == 5
        assert window.cursor.value() == 1
        assert window.live_button.isEnabled()
        window.live_button.click()
        assert window.cursor_follows and window.cursor.value() == 4
        window.end_time.setValue(3)
        window.add_sample(sample(6))
        assert window.bounds()[1] == 3
        assert "FIXED TIME RANGE" in window.view_status.text()
        assert "00:00:06" in window.view_status.text()
        window.live_button.click()
        assert window.bounds()[1] == 6
    finally:
        window.close()


@pytest.mark.parametrize("key,capacity", [("memory_mib", 164), ("vram_mib", 8)])
def test_zoom_survives_samples_and_fullscreen_restores_the_same_chart(tmp_path, app, key, capacity):
    window = Window(tmp_path, auto_start=False)
    try:
        window.show()
        for t in range(1, 10):
            window.add_sample(sample(t))
        app.processEvents()
        plot = window.plots[key]
        assert plot.viewRange()[1] == pytest.approx([0, capacity])
        window.chart_buttons[key]["y_in"].click()
        zoom = plot.viewRange()[1]
        assert zoom[1] - zoom[0] == pytest.approx(capacity / 2)
        window.add_sample(sample(10))
        assert plot.viewRange()[1] == pytest.approx(zoom)
        assert window.follow.isChecked() and window.bounds()[1] == 10
        window.chart_buttons[key]["fullscreen"].click()
        app.processEvents()
        assert window.fullscreen_dialog.isFullScreen()
        assert plot.height() > 180
        window.chart_buttons[key]["fit_y"].click()
        fitted = plot.viewRange()[1]
        assert fitted[1] - fitted[0] < 1
        window.add_sample(sample(11))
        assert plot.viewRange()[1] == pytest.approx(fitted)
        assert window.curves[key].getData()[0][-1] == 11
        QTest.keyClick(window.fullscreen_dialog, Qt.Key.Key_Escape)
        app.processEvents()
        assert window.fullscreen_dialog is None
        assert window.plots[key] is plot and plot.height() == 180
        window.chart_buttons[key]["x_in"].click()
        assert not window.follow.isChecked()
        bounds = window.bounds()
        window.add_sample(sample(12))
        assert window.bounds() == bounds
        window.chart_buttons[key]["reset"].click()
        assert window.follow.isChecked() and window.bounds()[1] == 12
        assert plot.viewRange()[1] == pytest.approx([0, capacity])
    finally:
        window.close()


def test_mouse_wheel_on_y_axis_keeps_time_following(tmp_path, app):
    window = Window(tmp_path, auto_start=False)
    try:
        window.show()
        window.add_sample(sample(1))
        app.processEvents()
        plot = window.plots["memory_mib"]
        axis = plot.getAxis("left")
        pos = plot.mapFromScene(axis.mapToScene(axis.geometry().center() - axis.pos()))
        wheel = QWheelEvent(QPointF(pos), QPointF(plot.viewport().mapToGlobal(pos)),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        app.sendEvent(plot.viewport(), wheel)
        app.processEvents()
        assert "memory_mib" in window.manual_y
        zoom = plot.viewRange()[1]
        assert zoom != [0, 164]
        assert window.follow.isChecked()
        window.add_sample(sample(2))
        assert plot.viewRange()[1] == pytest.approx(zoom)
    finally:
        window.close()


def test_record_clock_markers_and_live_updates_survive_interaction(tmp_path, app, monkeypatch):
    monkeypatch.setattr("chequebo.monitor.SystemSampler", FakeSampler)
    window = Window(tmp_path)
    window.interval.setValue(.2)
    window.show()
    try:
        until(lambda: len(window.samples) >= 2, app)
        window.follow.setChecked(False)
        window.start_button.click()
        until(lambda: window.recording, app)
        assert window.follow.isChecked()
        start = window.recording_span["live_started_elapsed"]
        for first, last in window.record_lines.values():
            assert first.value() == start and first.isVisible() and not last.isVisible()
        window.select_time(0)
        before = window.samples[-1]["elapsed"]
        window.chart_buttons["cpu_percent"]["fullscreen"].click()
        until(lambda: window.samples[-1]["elapsed"] > before + 1, app)
        assert window.record_clock.text().startswith("● Recording · ")
        assert not window.record_clock.text().endswith("00:00:00")
        assert window.curves["cpu_percent"].getData()[0][-1] > before
        assert "Recording" in window.fullscreen_status.text()
        window.chart_buttons["cpu_percent"]["fullscreen"].click()
        window.stop_button.click()
        until(lambda: window.last_recording is not None, app)
        duration = window.recording_span["duration"]
        clock_text = window.record_clock.text()
        for first, last in window.record_lines.values():
            assert last.isVisible() and last.value() == pytest.approx(start + duration)
        before = window.samples[-1]["elapsed"]
        until(lambda: window.samples[-1]["elapsed"] > before + .4, app)
        assert window.record_clock.text() == clock_text
        metadata, recorded = load_session(window.last_recording)
        assert metadata["duration"] >= recorded[-1]["elapsed"]
        assert metadata["live_ended_elapsed"] == pytest.approx(start + duration)
        window.load_recording(window.last_recording)
        for first, last in window.record_lines.values():
            assert first.value() == 0 and last.value() == duration
        window.start_button.click()
        until(lambda: window.recording, app)
        assert window.viewing_live and window.follow.isChecked()
        assert window.recording_span["live_started_elapsed"] > start
        assert not window.record_lines["memory_mib"][1].isVisible()
    finally:
        window.close()
        assert not window.monitor.isRunning()


def test_recorded_vram_capacity_uses_the_recorded_gpu(tmp_path, app):
    path = tmp_path / "gpu.chequebo"
    writer = SessionWriter(path, {"scope": "system", "cpu_count": 8})
    writer.append({**sample(1), "vram_total_mib": 24*1024})
    writer.close()
    window = Window(tmp_path, auto_start=False)
    try:
        window.add_sample(sample(1))
        window.load_recording(path)
        assert window.plots["vram_mib"].viewRange()[1] == pytest.approx([0, 24])
        assert window.value_labels["vram_mib"].text() == "2.00 / 24.00 GiB used"
        window.show_live()
        assert window.plots["vram_mib"].viewRange()[1] == pytest.approx([0, 8])
    finally:
        window.close()
