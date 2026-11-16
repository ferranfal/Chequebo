from PySide6.QtCore import Qt
from chequebo.ui import Window


def samples(cpus):
    return [dict(elapsed=t, cpu_percent=cpu, cpu_equivalent=cpu*cpus/100,
                 cpu_system=[i % 101 for i in range(cpus)], ram_mib=ram, swap_mib=0,
                 gpu_percent=None, vram_mib=None, read_mib_s=0, write_mib_s=1,
                 processes=1, threads=2)
            for t, cpu, ram in [(1, 20, 100), (2, 50, 40), (3, 10, 10)]]


def test_window_supports_other_cpu_counts_and_peak_navigation(tmp_path, app):
    window = Window(tmp_path)
    try:
        window.metadata = {"cpu_count": 64, "physical_cores": 32}
        window.samples = samples(64)
        window._core_layout(64)
        window.show()
        window.refresh()
        app.processEvents()
        assert len(window.core_bars) == 64
        window.start_time.setValue(2)
        window.end_time.setValue(3)
        assert window.table.item(0, 1).text() == "100.00 MiB"
        assert window.table.item(0, 3).text() == "40.00 MiB"
        window.jump_to_peak(0, 1)
        assert window.cursor.value() == 0
        assert window.bounds()[0] == 0
        assert window.value_labels["gpu_percent"].text() == "No disponible"
        assert window.table.item(0, 1).data(Qt.ItemDataRole.UserRole) == 1
    finally:
        window.close()

