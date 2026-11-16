import csv
import pytest
from chequebo.session import SessionWriter, load_session, maximum, export_maxima


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
    assert rows[0]["maximo_sesion"] == "100"
    assert rows[0]["maximo_intervalo"] == "20"
    assert rows[5]["maximo_sesion"] == ""

