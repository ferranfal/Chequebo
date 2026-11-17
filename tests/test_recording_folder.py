from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings

from chequebo.preferences import recording_directory
from chequebo.session import load_session
from chequebo.ui import Window
from test_system import FakeSampler, until


def settings_at(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_folder_picker_remembers_selection_and_explicit_override_wins(tmp_path, app, monkeypatch):
    config = tmp_path / "preferences.ini"
    folder = tmp_path / "Logs amb espais à"
    folder.mkdir()
    initial = tmp_path / "initial"
    window = Window(initial, auto_start=False, settings=settings_at(config))
    monkeypatch.setattr("chequebo.ui.QFileDialog.getExistingDirectory", lambda *args: str(folder))
    try:
        window.folder_button.click()
        assert window.data_dir == folder
        assert window.folder_label.text() == str(folder)
        assert list(folder.iterdir()) == []  # The write probe leaves no files behind.
    finally:
        window.close()
    reopened = Window(auto_start=False, settings=settings_at(config))
    explicit = Window(initial, auto_start=False, settings=settings_at(config))
    try:
        assert reopened.data_dir == folder
        assert reopened.new_path().parent == folder
        assert explicit.data_dir == initial
        assert recording_directory(settings=settings_at(config)) == folder
        assert recording_directory(initial, settings_at(config)) == initial
        assert settings_at(config).value("recording_folder") == str(folder)
    finally:
        reopened.close()
        explicit.close()


@pytest.mark.parametrize("failure", ["cancel", "unwritable", "missing"])
def test_cancel_or_invalid_folder_keeps_previous_destination(tmp_path, app, monkeypatch, failure):
    config = tmp_path / "preferences.ini"
    initial = tmp_path / "initial"
    window = Window(initial, auto_start=False, settings=settings_at(config))
    warnings = []
    monkeypatch.setattr("chequebo.ui.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    selected = "" if failure == "cancel" else str(tmp_path / "missing" if failure == "missing" else tmp_path)
    monkeypatch.setattr("chequebo.ui.QFileDialog.getExistingDirectory", lambda *args: selected)
    if failure == "unwritable":
        def denied(**kwargs):
            raise PermissionError("Permission denied")
        monkeypatch.setattr("chequebo.ui.tempfile.TemporaryFile", denied)
    try:
        window.folder_button.click()
        assert window.data_dir == initial
        assert window.folder_label.text() == str(initial)
        assert not config.exists()
        assert len(warnings) == (0 if failure == "cancel" else 1)
    finally:
        window.close()


def test_changing_folder_during_recording_only_affects_next_session(tmp_path, app, monkeypatch):
    monkeypatch.setattr("chequebo.monitor.SystemSampler", FakeSampler)
    initial, selected = tmp_path / "first", tmp_path / "second"
    selected.mkdir()
    window = Window(initial, settings=settings_at(tmp_path / "preferences.ini"))
    window.interval.setValue(.2)
    monkeypatch.setattr("chequebo.ui.QFileDialog.getExistingDirectory", lambda *args: str(selected))
    try:
        until(lambda: len(window.samples) >= 2, app)
        window.start_button.click()
        until(lambda: window.recording, app)
        first = Path(window.monitor.path)
        assert first.parent == initial
        window.folder_button.click()
        assert window.recording and Path(window.monitor.path) == first
        count = len(window.samples)
        until(lambda: len(window.samples) >= count + 2, app)
        assert not list(selected.iterdir())
        window.stop_button.click()
        until(lambda: not window.recording, app)
        assert window.last_recording == first
        first_metadata, first_samples = load_session(first)
        assert first_metadata["state"] == "stopped" and len(first_samples) >= 2
        window.start_button.click()
        until(lambda: window.recording, app)
        second = Path(window.monitor.path)
        assert second.parent == selected
        count = len(window.samples)
        until(lambda: len(window.samples) >= count + 2, app)
        window.stop_button.click()
        until(lambda: not window.recording, app)
        assert window.last_recording == second
        assert len(load_session(second)[1]) >= 2
        assert load_session(first) == (first_metadata, first_samples)
        count = len(window.samples)
        until(lambda: len(window.samples) > count, app)
        assert window.monitor.isRunning()
    finally:
        window.monitor.stop()
        assert window.monitor.wait(3000)
        app.processEvents()
        window.close()


def test_unavailable_saved_folder_does_not_prevent_live_monitoring(tmp_path, app, monkeypatch):
    monkeypatch.setattr("chequebo.monitor.SystemSampler", FakeSampler)
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("existing file")
    settings = settings_at(tmp_path / "preferences.ini")
    settings.setValue("recording_folder", str(blocked))
    window = Window(settings=settings)
    window.interval.setValue(.2)
    try:
        until(lambda: len(window.samples) >= 2, app)
        window.start_button.click()
        until(lambda: "Recording error:" in window.record_status.text(), app)
        count = len(window.samples)
        until(lambda: len(window.samples) > count, app)
        assert window.monitor.isRunning() and window.start_button.isEnabled()
        assert blocked.read_text() == "existing file"
    finally:
        window.monitor.stop()
        assert window.monitor.wait(3000)
        app.processEvents()
        window.close()
