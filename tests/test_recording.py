import os
import sys
import time

from chequebo.recorder import Recorder, launch
from chequebo.session import load_session, maximum


def test_record_real_workload_and_persist_memory_peak(tmp_path, app):
    path = tmp_path / "real.chequebo"
    code = "import time; a=bytearray(32*1024*1024); time.sleep(.8); del a; time.sleep(.8)"
    process, gate = launch([sys.executable, "-c", code], tmp_path, tmp_path / "real.log")
    recorder = Recorder(process.pid, path, .2, code, process, gate)
    recorder.start()
    assert recorder.wait(8000)
    metadata, samples = load_session(path)
    assert metadata["state"] == "finished"
    assert metadata["exit_code"] == 0
    assert len(samples) >= 5
    assert maximum(samples, "ram_mib")["ram_mib"] > samples[-1]["ram_mib"] + 20
    assert all(sample["processes"] >= 1 for sample in samples)


def test_stopping_recorder_does_not_stop_process(tmp_path, app):
    path = tmp_path / "stop.chequebo"
    process, gate = launch([sys.executable, "-c", "import time; time.sleep(8)"], tmp_path, tmp_path / "stop.log")
    recorder = Recorder(process.pid, path, .2, "sleep", process, gate)
    try:
        recorder.start()
        time.sleep(.7)
        recorder.stop()
        assert recorder.wait(4000)
        assert process.poll() is None
        assert load_session(path)[0]["state"] == "stopped"
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_nonexistent_command_reports_exit_code_without_fake_samples(tmp_path, app):
    path = tmp_path / "bad.chequebo"
    process, gate = launch(["/chequebo/nonexistent-executable"], tmp_path, tmp_path / "bad.log")
    recorder = Recorder(process.pid, path, .2, "bad", process, gate)
    recorder.start()
    assert recorder.wait(4000)
    metadata, _ = load_session(path)
    assert metadata["exit_code"] != 0


def test_packaged_launcher_uses_system_python_and_restores_libraries(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/opt/chequebo/_internal", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/chequebo/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/opt/chequebo/_internal/plugins")
    code = "import os; print(os.environ.get('LD_LIBRARY_PATH')); print(os.environ.get('QT_PLUGIN_PATH'))"
    log = tmp_path / "packaged.log"
    process, gate = launch(["/usr/bin/python3", "-c", code], tmp_path, log)
    os.write(gate, b"1")
    os.close(gate)
    assert process.wait(timeout=5) == 0
    assert log.read_text().splitlines() == ["/usr/lib", "None"]
