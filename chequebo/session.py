"""SQLite recordings: one sample per row, no server or custom binary format."""

import csv
import json
import math
import sqlite3
from pathlib import Path

# key, title, unit. None means unavailable and is never converted to zero.
METRICS = (
    ("ram_mib", "Program RAM (PSS)", "MiB"),
    ("swap_mib", "Program swap", "MiB"),
    ("cpu_percent", "Program CPU / total capacity", "%"),
    ("read_mib_s", "Disk read", "MiB/s"),
    ("write_mib_s", "Disk write", "MiB/s"),
    ("gpu_percent", "GPU · whole system", "%"),
    ("vram_mib", "Program VRAM · NVIDIA", "MiB"),
    ("processes", "Concurrent observed processes", ""),
    ("threads", "Program threads", ""),
    ("memory_mib", "Program RAM + swap", "MiB"),
)

SYSTEM_METRICS = (
    ("ram_mib", "System RAM (total − available)", "MiB"),
    ("swap_mib", "System swap", "MiB"),
    ("cpu_percent", "System CPU / total capacity", "%"),
    ("read_mib_s", "System disk read", "MiB/s"),
    ("write_mib_s", "System disk write", "MiB/s"),
    ("gpu_percent", "GPU · whole system", "%"),
    ("vram_mib", "VRAM · whole system", "MiB"),
    ("processes", "System processes", ""),
    ("memory_mib", "System RAM + swap", "MiB"),
)


def metrics_for(metadata):
    return SYSTEM_METRICS if metadata.get("scope") == "system" else METRICS


def display_metric(key, value, unit):
    """Keep recordings/raw CSV in their original units; present memory in GiB."""
    if key in ("ram_mib", "swap_mib", "memory_mib", "vram_mib"):
        return (value / 1024 if value is not None else None), "GiB"
    return value, unit


def metric_value(sample, key):
    if key == "memory_mib":
        values = (sample.get("ram_mib"), sample.get("swap_mib"))
        if all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            return sum(values)
        return None
    return sample.get(key)


def clock(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def maximum(samples, key, start=None, end=None):
    """Maximum of aggregated samples; return the earliest sample on a tie."""
    candidates = (
        (s, metric_value(s, key)) for s in samples
        if (start is None or s["elapsed"] >= start)
        and (end is None or s["elapsed"] <= end)
    )
    peak = max(((sample, value) for sample, value in candidates
                if isinstance(value, (int, float)) and math.isfinite(value)),
               key=lambda pair: pair[1], default=None)
    return {**peak[0], key: peak[1]} if peak else None


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
        self.connection.commit()  # Every committed sample survives an unexpected shutdown.

    def close(self):
        self.connection.close()


def load_session(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        metadata = {k: json.loads(v) for k, v in connection.execute("SELECT key, value FROM meta")}
        if metadata.get("format") != "chequebo" or metadata.get("version") != 1:
            raise ValueError("This file is not a supported Chequebo recording.")
        samples = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM samples ORDER BY seq"
        )]
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get("elapsed"), (float, int)):
                raise ValueError("The recording contains invalid samples.")
        return metadata, samples
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read the recording: {exc}") from exc
    finally:
        connection.close()


def export_samples(path, samples, metadata=None):
    keys = ["timestamp", "elapsed", *[key for key, _, _ in metrics_for(metadata or {})], "cpu_equivalent"]
    with open(path, "w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: metric_value(sample, key) for key in keys} for sample in samples)


def export_maxima(path, samples, start, end, metadata=None):
    with open(path, "w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["resource", "unit", "recording_peak", "recording_peak_second",
                         "range_peak", "range_peak_second", "from_second", "to_second"])
        for key, title, unit in metrics_for(metadata or {}):
            whole, selected = maximum(samples, key), maximum(samples, key, start, end)
            whole_value, display_unit = display_metric(key, whole[key] if whole else None, unit)
            selected_value, _ = display_metric(key, selected[key] if selected else None, unit)
            writer.writerow([title, display_unit,
                             whole_value if whole else "", whole["elapsed"] if whole else "",
                             selected_value if selected else "", selected["elapsed"] if selected else "",
                             start, end])
