import csv
import pytest
from chequebo.session import SessionWriter, load_session, maximum, export_maxima, export_samples


def test_maxima_are_independent_and_keep_first_tie():
    samples = [{"elapsed": 1, "ram_mib": 80, "cpu_percent": 10},
               {"elapsed": 2, "ram_mib": 10, "cpu_percent": 80},
               {"elapsed": 3, "ram_mib": 80, "cpu_percent": None}]
    assert maximum(samples, "ram_mib")["elapsed"] == 1
    assert maximum(samples, "cpu_percent")["elapsed"] == 2
    assert maximum(samples, "ram_mib", 1.5, 2.5)["ram_mib"] == 10
    assert maximum(samples, "gpu_percent") is None
    assert maximum(samples, "cpu_percent", 3, 3) is None


def test_committed_samples_survive_unfinished_session(tmp_path):
    path = tmp_path / "sesión con espacios.chequebo"
    store = SessionWriter(path, {"state": "recording", "cpu_count": 64})
    store.append({"elapsed": 0.5, "ram_mib": None, "cpu_percent": 0})
    meta, samples = load_session(path)
    assert meta["state"] == "recording"
    assert samples[0]["ram_mib"] is None
    assert samples[0]["cpu_percent"] == 0
    store.close()
    assert load_session(path)[0]["cpu_count"] == 64


def test_invalid_session_rejected(tmp_path):
    path = tmp_path / "invalid.chequebo"
    path.write_text("not a database")
    with pytest.raises(ValueError):
        load_session(path)


def test_maxima_csv_preserves_missing_and_session_peak(tmp_path):
    path = tmp_path / "maxima.csv"
    export_maxima(path, [{"elapsed": 1, "ram_mib": 100}, {"elapsed": 2, "ram_mib": 20}], 2, 2)
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["unit"] == "GiB"
    assert float(rows[0]["recording_peak"]) == 100 / 1024
    assert float(rows[0]["range_peak"]) == 20 / 1024
    assert rows[5]["recording_peak"] == ""


def test_combined_memory_peak_uses_simultaneous_usage_and_exports_units(tmp_path):
    samples = [{"elapsed": 1, "ram_mib": 64*1024, "swap_mib": 0},
               {"elapsed": 2, "ram_mib": 48*1024, "swap_mib": 24*1024},
               {"elapsed": 3, "ram_mib": 32*1024, "swap_mib": 32*1024},
               {"elapsed": 4, "ram_mib": 64*1024, "swap_mib": None}]
    peak = maximum(samples, "memory_mib")
    assert peak["elapsed"] == 2 and peak["memory_mib"] == 72*1024
    assert maximum(samples, "memory_mib", 4, 4) is None
    assert "memory_mib" not in samples[1]
    path = tmp_path / "peaks.csv"
    export_maxima(path, samples, 3, 3, {"scope": "system"})
    rows = list(csv.DictReader(path.open()))
    memory = next(row for row in rows if row["resource"] == "System RAM + swap")
    assert memory["unit"] == "GiB"
    assert float(memory["recording_peak"]) == 72
    assert float(memory["range_peak"]) == 64
    export_samples(path, samples, {"scope": "system"})
    rows = list(csv.DictReader(path.open()))
    assert float(rows[1]["memory_mib"]) == 72*1024
    assert rows[3]["memory_mib"] == ""
