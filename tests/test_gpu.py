from types import SimpleNamespace as NS
from chequebo.metrics import GPU, MIB


def fake_gpu(records):
    gpu = GPU.__new__(GPU)
    gpu.handles = [0]
    gpu.note = "test"
    gpu.nv = NS(NVMLError=RuntimeError,
                nvmlDeviceGetUtilizationRates=lambda _: NS(gpu=37),
                nvmlDeviceGetComputeRunningProcesses=lambda _: records,
                nvmlDeviceGetGraphicsRunningProcesses=lambda _: records)
    return gpu


def test_vram_filters_pids_and_deduplicates_graphics_compute():
    gpu = fake_gpu([NS(pid=1, usedGpuMemory=64*MIB), NS(pid=2, usedGpuMemory=1024*MIB)])
    assert gpu.sample({1}) == (37, 64)
    assert gpu.sample({3}) == (37, 0)


def test_vram_unavailable_is_not_zero_or_a_huge_peak():
    gpu = fake_gpu([NS(pid=1, usedGpuMemory=(1 << 64)-1)])
    assert gpu.sample({1}) == (37, None)


def test_partial_gpu_failure_is_not_attributed_to_process():
    gpu = fake_gpu([])
    def unsupported(_):
        raise RuntimeError("not supported")
    gpu.nv.nvmlDeviceGetGraphicsRunningProcesses = unsupported
    assert gpu.sample({1}) == (37, None)


def test_system_vram_uses_device_memory_instead_of_process_lists():
    gpu = fake_gpu([NS(pid=1, usedGpuMemory=64*MIB)])
    gpu.handles = [0, 1]
    gpu.nv.nvmlDeviceGetMemoryInfo = lambda card: NS(used=(card+1)*1024*MIB)
    assert gpu.sample() == (37, 3072)
    def unsupported(_):
        raise RuntimeError("unavailable")
    gpu.nv.nvmlDeviceGetMemoryInfo = unsupported
    assert gpu.sample() == (37, None)


def test_nvidia_capacity_sums_cards_and_preserves_unavailable():
    gpu = fake_gpu([])
    gpu.handles = [0, 1]
    gpu.nv.nvmlDeviceGetMemoryInfo = lambda card: NS(total=(card + 1)*8*1024*MIB)
    assert gpu.total_memory_mib() == 24*1024
    def unavailable(_):
        raise RuntimeError("GPU lost")
    gpu.nv.nvmlDeviceGetMemoryInfo = unavailable
    assert gpu.total_memory_mib() is None


def test_amd_capacity_reads_driver_totals(tmp_path):
    gpu = GPU.__new__(GPU)
    gpu.handles = []
    gpu.amd = [tmp_path / "gpu_busy_percent"]
    total = tmp_path / "mem_info_vram_total"
    total.write_text(str(16*1024*MIB))
    assert gpu.total_memory_mib() == 16*1024
    total.unlink()
    assert gpu.total_memory_mib() is None
