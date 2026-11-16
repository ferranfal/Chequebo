import os
import subprocess
import sys
import time
from types import SimpleNamespace as NS

import psutil
import pytest

from chequebo import metrics


class NoGPU:
    note = "GPU no disponible"
    def sample(self, pids):
        return None, None
    def close(self):
        pass


@pytest.mark.parametrize("cpus", [1, 4, 8, 16, 64, 128])
def test_cpu_percentage_uses_detected_logical_count(monkeypatch, cpus):
    clock = [10.0]
    ticks = [0.0]
    monkeypatch.setattr(metrics, "GPU", NoGPU)
    monkeypatch.setattr(metrics.psutil, "cpu_count", lambda: cpus)
    monkeypatch.setattr(metrics.psutil, "cpu_percent", lambda **_: [0] * cpus)
    monkeypatch.setattr(metrics.time, "monotonic", lambda: clock[0])
    sampler = metrics.Sampler(os.getpid())
    fake = NS(pid=123, cpu_times=lambda: NS(user=ticks[0], system=0),
              num_threads=lambda: 1, memory_info=lambda: NS(rss=1024),
              memory_full_info=lambda: NS(pss=1024, swap=0),
              io_counters=lambda: NS(read_bytes=0, write_bytes=0))
    sampler.tree.alive = lambda: {(123, 1): fake}
    assert sampler.sample()["cpu_percent"] is None
    clock[0] += 1
    ticks[0] += 1
    sample = sampler.sample()
    assert sample["cpu_equivalent"] == 1
    assert sample["cpu_percent"] == pytest.approx(100 / cpus)
    assert len(sample["cpu_system"]) == cpus
    assert sample["gpu_percent"] is None


def test_process_identity_reuse_is_not_followed():
    tree = metrics.ProcessTree(os.getpid())
    dead = NS(is_running=lambda: False)
    tree.known = {(123456, 1): dead}
    assert tree.alive() == {}


def test_own_session_finds_orphaned_child(tmp_path):
    path = tmp_path / "pid"
    command = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(8)']); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(0.5)"
    parent = subprocess.Popen([sys.executable, "-c", command, str(path)], start_new_session=True)
    tree = metrics.ProcessTree(parent.pid, own_session=True)
    child_pid = None
    try:
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        child_pid = int(path.read_text())
        parent.wait(timeout=3)
        assert child_pid in [p.pid for p in tree.alive().values()]
    finally:
        if child_pid:
            try:
                psutil.Process(child_pid).terminate()
            except psutil.NoSuchProcess:
                pass
        if parent.poll() is None:
            parent.terminate()
            parent.wait()

