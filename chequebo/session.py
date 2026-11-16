"""Sesiones SQLite: una muestra por fila, sin servidor ni formatos binarios propios."""

import csv
import json
import math
import sqlite3
from pathlib import Path

# key, título, unidad. None significa no disponible; nunca se convierte en cero.
METRICS = (
    ("ram_mib", "RAM del programa (PSS)", "MiB"),
    ("swap_mib", "Swap del programa", "MiB"),
    ("cpu_percent", "CPU del programa / total del equipo", "%"),
    ("read_mib_s", "Lectura de disco", "MiB/s"),
    ("write_mib_s", "Escritura de disco", "MiB/s"),
    ("gpu_percent", "GPU · equipo completo", "%"),
    ("vram_mib", "VRAM del programa · NVIDIA", "MiB"),
    ("processes", "Procesos observados simultáneamente", ""),
    ("threads", "Hilos del programa", ""),
)


def clock(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def maximum(samples, key, start=None, end=None):
    """Máximo de las muestras agregadas; en un empate devuelve el primer instante."""
    candidates = (
        s for s in samples
        if (start is None or s["elapsed"] >= start)
        and (end is None or s["elapsed"] <= end)
        and isinstance(s.get(key), (int, float))
        and math.isfinite(s[key])
    )
    return max(candidates, key=lambda s: s[key], default=None)


class SessionWriter:
    def __init__(self, path, metadata):
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        self.connection.execute(
            "CREATE TABLE samples (seq INTEGER PRIMARY KEY, elapsed REAL, payload TEXT)"
        )
        self.set_meta({"format": "chequebo", "version": 1, **metadata})

    def set_meta(self, metadata):
        self.connection.executemany(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        self.connection.commit()

    def append(self, sample):
        self.connection.execute(
            "INSERT INTO samples (elapsed, payload) VALUES (?, ?)",
            (sample["elapsed"], json.dumps(sample, allow_nan=False)),
        )
        self.connection.commit()  # Cada muestra sobrevive a un cierre inesperado.

    def close(self):
        self.connection.close()


def load_session(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        metadata = {k: json.loads(v) for k, v in connection.execute("SELECT key, value FROM meta")}
        if metadata.get("format") != "chequebo" or metadata.get("version") != 1:
            raise ValueError("Este archivo no es una sesión compatible de Chequebo.")
        samples = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM samples ORDER BY seq"
        )]
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get("elapsed"), (float, int)):
                raise ValueError("La sesión contiene muestras inválidas.")
        return metadata, samples
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError(f"No se puede leer la sesión: {exc}") from exc
    finally:
        connection.close()


def export_samples(path, samples):
    keys = ["timestamp", "elapsed", *[key for key, _, _ in METRICS], "cpu_equivalent"]
    with open(path, "w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(samples)


def export_maxima(path, samples, start, end):
    with open(path, "w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["recurso", "unidad", "maximo_sesion", "segundo_sesion",
                         "maximo_intervalo", "segundo_intervalo", "desde", "hasta"])
        for key, title, unit in METRICS:
            whole, selected = maximum(samples, key), maximum(samples, key, start, end)
            writer.writerow([title, unit,
                             whole[key] if whole else "", whole["elapsed"] if whole else "",
                             selected[key] if selected else "", selected["elapsed"] if selected else "",
                             start, end])

