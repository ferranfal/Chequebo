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

