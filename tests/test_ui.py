from PySide6.QtCore import Qt
import pytest
from chequebo.session import SessionWriter
from chequebo.ui import Window


def samples(cpus):
    return [dict(elapsed=t, cpu_percent=cpu, cpu_equivalent=cpu*cpus/100,
                 cpu_system=[i % 101 for i in range(cpus)], ram_mib=ram, swap_mib=0,
                 gpu_percent=None, gpu_note="Test GPU unavailable", vram_mib=None, read_mib_s=0, write_mib_s=1,
                 processes=1, threads=2)
            for t, cpu, ram in [(1, 20, 100), (2, 50, 40), (3, 10, 10)]]


def test_window_supports_other_cpu_counts_and_peak_navigation(tmp_path, app):
    window = Window(tmp_path, auto_start=False)
    try:
        window.viewing_live = False
        window.metadata = {"cpu_count": 64, "physical_cores": 32}
        window.samples = samples(64)
        window._core_layout(64)
        window.show()
        window.refresh()
        app.processEvents()
        assert len(window.core_bars) == 64
        window.start_time.setValue(2)
        window.end_time.setValue(3)
        assert window.table.item(0, 1).text() == "0.10 GiB"
        assert window.table.item(0, 3).text() == "0.04 GiB"
        window.jump_to_peak(0, 1)
        assert window.cursor.value() == 0
        assert window.bounds()[0] == 0
        assert window.value_labels["gpu_percent"].text() == "Unavailable"
        assert window.table.item(0, 1).data(Qt.ItemDataRole.UserRole) == 1
    finally:
        window.close()


def test_memory_chart_uses_gib_and_tracks_configured_capacity(tmp_path, app):
    window = Window(tmp_path, auto_start=False)
    try:
        window.show()
        for index, swap_total in enumerate([100, 200, 0]):
            sample = {**samples(8)[0], "elapsed": index + 1,
                      "ram_mib": 48 * 1024, "swap_mib": min(24, swap_total) * 1024,
                      "ram_total_mib": 64 * 1024, "swap_total_mib": swap_total * 1024}
            window.add_sample(sample)
            app.processEvents()
            assert window.plots["memory_mib"].viewRange()[1] == pytest.approx([0, 64 + swap_total])
            assert window.ram_limit.value() == 64
            assert window.ram_limit.isVisible()
            assert window.curves["memory_mib"].getData()[1][-1] == 48 + min(24, swap_total)
            assert f"/ {64 + swap_total:.2f} GiB" in window.value_labels["memory_mib"].text()
        window.select_time(1)
        assert "RAM 48.00 + swap 24.00 GiB" in window.value_labels["memory_mib"].text()
        assert window.table.item(0, 1).text() == "48.00 GiB"
        assert window.table.item(1, 1).text() == "24.00 GiB"
        assert window.table.item(8, 1).text() == "72.00 GiB"
    finally:
        window.close()


def test_recorded_memory_capacity_is_independent_of_current_computer(tmp_path, app):
    path = tmp_path / "different-computer.chequebo"
    writer = SessionWriter(path, {"scope": "system", "cpu_count": 8,
                                 "ram_total_mib": 16 * 1024, "swap_total_mib": 4 * 1024})
    writer.append({**samples(8)[0], "ram_total_mib": 16 * 1024, "swap_total_mib": 8 * 1024})
    writer.close()
    window = Window(tmp_path, auto_start=False)
    try:
        window.live_metadata.update(ram_total_mib=64 * 1024, swap_total_mib=100 * 1024)
        window.load_recording(path)
        assert window.plots["memory_mib"].viewRange()[1] == pytest.approx([0, 24])
        assert window.ram_limit.value() == 16
        window.add_sample({**samples(8)[0], "ram_total_mib": 64 * 1024, "swap_total_mib": 200 * 1024})
        assert window.plots["memory_mib"].viewRange()[1] == pytest.approx([0, 24])
        window.show_live()
        assert window.plots["memory_mib"].viewRange()[1] == pytest.approx([0, 264])
        assert window.ram_limit.value() == 64
    finally:
        window.close()
