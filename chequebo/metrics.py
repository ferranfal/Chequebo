"""Unprivileged sampling. CPU and I/O use counter differences between samples."""

import os
import time
from pathlib import Path

import psutil

MIB = 1024 ** 2
GONE = (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError)


class ProcessTree:
    """Identify each process by PID + creation time to guard against PID reuse.

    Launched programs also use their own POSIX session to retain orphan children.
    Attaching can only discover children that exist during a sample.
    """

    def __init__(self, pid, own_session=False):
        root = psutil.Process(pid)
        self.known = {(root.pid, root.create_time()): root}
        self.sid = pid if own_session else None
        self.uid = os.getuid()

    def alive(self):
        discovered = dict(self.known)
        for process in list(self.known.values()):
            try:
                if process.is_running():
                    for child in process.children(recursive=True):
                        discovered[(child.pid, child.create_time())] = child
            except (psutil.Error, ProcessLookupError):
                pass
        if self.sid is not None:
            for process in psutil.process_iter(["pid", "uids", "create_time"], ad_value=None):
                try:
                    uid = process.info["uids"]
                    if uid and uid.real == self.uid and os.getsid(process.pid) == self.sid:
                        discovered[(process.pid, process.create_time())] = process
                except (psutil.Error, ProcessLookupError, PermissionError):
                    continue
        alive = {}
        for identity, process in discovered.items():
            try:
                if process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    alive[identity] = process
            except GONE:
                pass
        self.known = alive
        return alive


class GPU:
    """Global GPU + tree VRAM for NVIDIA; global AMD via gpu_busy_percent.

    Global GPU activity is not attributed to the program. Multiple devices use
    the mean utilization and the sum of their attributed VRAM.
    """

    def __init__(self):
        self.nv = None
        self.handles = []
        self.amd = list(Path("/sys/class/drm").glob("card[0-9]*/device/gpu_busy_percent"))
        self.note = "GPU unavailable: unsupported driver or insufficient permissions."
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i)
                            for i in range(pynvml.nvmlDeviceGetCount())]
            if self.handles:
                self.note = "NVIDIA: whole-system activity; VRAM of observed processes."
        except (ImportError, RuntimeError, OSError) as exc:
            self.note = f"GPU unavailable: {exc}"
        except Exception as exc:
            # NVML has its own exceptions; GPU monitoring is optional.
            self.note = f"GPU unavailable: {exc}"

    def sample(self, pids=None):
        """None selects whole-device VRAM; a PID set selects process VRAM."""
        if not self.handles:
            try:
                values = [int(path.read_text()) for path in self.amd]
                if values:
                    self.note = "AMD: whole-system activity; VRAM when exposed by the driver."
                memory = None
                if values and pids is None:
                    try:
                        memory = sum(int((path.parent / "mem_info_vram_used").read_text())
                                     for path in self.amd) / MIB
                    except (OSError, ValueError):
                        pass
                return (sum(values) / len(values) if values else None), memory
            except (OSError, ValueError):
                return None, None
        activity, memory = [], []
        valid_activity = valid_memory = True
        for handle in self.handles:
            try:
                activity.append(self.nv.nvmlDeviceGetUtilizationRates(handle).gpu)
            except self.nv.NVMLError:
                valid_activity = False
            if pids is None:
                self.note = "NVIDIA: whole-system GPU activity and VRAM usage."
                try:
                    memory.append(self.nv.nvmlDeviceGetMemoryInfo(handle).used)
                except self.nv.NVMLError:
                    valid_memory = False
                continue
            records = {}
            for query in (self.nv.nvmlDeviceGetComputeRunningProcesses,
                          self.nv.nvmlDeviceGetGraphicsRunningProcesses):
                try:
                    for process in query(handle):
                        if process.pid in pids:
                            value = process.usedGpuMemory
                            # NVML represents "unavailable" as UINT64_MAX.
                            # Some pynvml versions do not export the constant.
                            if value is None or value < 0 or value >= (1 << 64) - 1:
                                valid_memory = False
                            else:
                                records[process.pid] = max(records.get(process.pid, 0), value)
                except self.nv.NVMLError:
                    valid_memory = False
            memory.append(sum(records.values()))
        return (sum(activity) / len(activity) if activity and valid_activity else None,
                sum(memory) / MIB if valid_memory else None)

    def total_memory_mib(self):
        """Capacity across the same devices used for the VRAM measurement."""
        if self.handles:
            try:
                return sum(self.nv.nvmlDeviceGetMemoryInfo(handle).total for handle in self.handles) / MIB
            except self.nv.NVMLError:
                return None
        try:
            return (sum(int((path.parent / "mem_info_vram_total").read_text()) for path in self.amd) / MIB
                    if self.amd else None)
        except (OSError, ValueError):
            return None

    def close(self):
        if self.nv:
            try:
                self.nv.nvmlShutdown()
            except self.nv.NVMLError:
                pass


class SystemSampler:
    """Whole-computer counters, independent of any selected program."""

    def __init__(self):
        self.started = self.previous = time.monotonic()
        self.previous_io = self._io()
        self.previous_network = self._network()
        self.first = True
        self.gpu = GPU()
        psutil.cpu_percent(percpu=True)

    @staticmethod
    def _io():
        try:
            return psutil.disk_io_counters()
        except (OSError, psutil.Error):
            return None

    @staticmethod
    def _network():
        """Prefer physical adapters to avoid counting VPN/bridge traffic twice."""
        try:
            counters = psutil.net_io_counters(pernic=True, nowrap=True)
            physical = [name for name in counters if (Path('/sys/class/net') / name / 'device').exists()]
            routes = []
            try:
                for line in Path('/proc/net/route').read_text().splitlines()[1:]:
                    parts = line.split()
                    if len(parts) > 3 and parts[1] == '00000000' and int(parts[3], 16) & 1:
                        routes.append(parts[0])
            except (OSError, ValueError):
                pass
            names = physical or [name for name in routes if name in counters] or [name for name in counters if name != 'lo']
            return {name: counters[name] for name in names}
        except (OSError, psutil.Error):
            return None

    def sample(self):
        now = time.monotonic()
        dt = max(now - self.previous, 0.000001)
        cpus = psutil.cpu_percent(percpu=True)
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        io = self._io()
        reads = writes = None
        network = self._network()
        received = sent = None
        if not self.first and network is not None and self.previous_network is not None:
            shared = network.keys() & self.previous_network.keys()
            if shared or not network:
                received = sum(max(0, network[n].bytes_recv - self.previous_network[n].bytes_recv) for n in shared) * 8 / dt / 1_000_000
                sent = sum(max(0, network[n].bytes_sent - self.previous_network[n].bytes_sent) for n in shared) * 8 / dt / 1_000_000
        if not self.first and io is not None and self.previous_io is not None:
            reads = max(0, io.read_bytes - self.previous_io.read_bytes) / dt / MIB
            writes = max(0, io.write_bytes - self.previous_io.write_bytes) / dt / MIB
        if self.first:
            cpus = []
        activity, vram = self.gpu.sample()
        result = {
            "timestamp": time.time(), "elapsed": now - self.started,
            "cpu_percent": sum(cpus) / len(cpus) if cpus else None,
            "cpu_equivalent": sum(cpus) / 100 if cpus else None,
            "cpu_system": cpus,
            "ram_mib": (memory.total - memory.available) / MIB,
            "ram_total_mib": memory.total / MIB,
            "swap_mib": swap.used / MIB,
            "swap_total_mib": swap.total / MIB,
            "read_mib_s": reads, "write_mib_s": writes,
            "net_recv_mbps": received, "net_sent_mbps": sent,
            "network_interfaces": sorted(network) if network is not None else [],
            "gpu_percent": activity, "vram_mib": vram,
            "vram_total_mib": self.gpu.total_memory_mib(),
            "processes": len(psutil.pids()),
            "gpu_note": self.gpu.note, "notes": [], "alive": True,
        }
        self.previous, self.previous_io, self.first = now, io, False
        self.previous_network = network
        return result

    def close(self):
        self.gpu.close()


class Sampler:
    def __init__(self, pid, own_session=False):
        self.tree = ProcessTree(pid, own_session)
        self.cpu_count = psutil.cpu_count() or 1
        self.started = self.previous = time.monotonic()
        self.previous_wall = time.time()
        self.counters = {}
        self.gpu = GPU()
        psutil.cpu_percent(percpu=True)  # First read establishes a baseline.

    def sample(self):
        now, wall = time.monotonic(), time.time()
        dt = max(now - self.previous, 0.000001)
        processes = self.tree.alive()
        cpu = read_bytes = write_bytes = ram = swap = rss = threads = 0
        valid_cpu = valid_io = valid_ram = valid_swap = True
        notes = set()
        current = {}
        observed = []
        for identity, process in processes.items():
            try:
                times = process.cpu_times()
                cpu_time = times.user + times.system  # No children_* counters: that would double-count.
                threads += process.num_threads()
                rss += process.memory_info().rss
                observed.append(process.pid)
            except GONE:
                continue
            except psutil.AccessDenied:
                valid_cpu = valid_io = valid_ram = valid_swap = False
                notes.add("Some processes cannot be read; incomplete metrics are left unavailable.")
                continue
            try:
                full = process.memory_full_info()
                ram += full.pss
                swap += full.swap
            except (psutil.Error, AttributeError):
                valid_ram = valid_swap = False
                notes.add("PSS/swap unavailable for the complete process tree.")
            try:
                io = process.io_counters()
                reads, writes = io.read_bytes, io.write_bytes
                if reads < 0 or writes < 0:
                    reads = writes = None
            except psutil.Error:
                reads = writes = None
            if reads is None:
                valid_io = False
            old = self.counters.get(identity)
            # A new child was born within this interval: its counters start at zero.
            if old is None and identity[1] >= self.previous_wall:
                old = (0, 0, 0)
            if old:
                cpu += max(0, cpu_time - old[0])
                if reads is not None and old[1] is not None:
                    read_bytes += max(0, reads - old[1])
                    write_bytes += max(0, writes - old[2])
            current[identity] = (cpu_time, reads, writes)
        first = not self.counters
        self.counters = current
        equivalent = cpu / dt if valid_cpu and not first else None
        gpu, vram = self.gpu.sample(set(observed))
        result = {
            "timestamp": wall, "elapsed": now - self.started,
            "cpu_percent": min(100.0, equivalent * 100 / self.cpu_count) if equivalent is not None else None,
            "cpu_equivalent": equivalent,
            "ram_mib": ram / MIB if valid_ram else None,
            "swap_mib": swap / MIB if valid_swap else None,
            "rss_mib": rss / MIB,
            "read_mib_s": read_bytes / dt / MIB if valid_io and not first else None,
            "write_mib_s": write_bytes / dt / MIB if valid_io and not first else None,
            "gpu_percent": gpu, "vram_mib": vram,
            "vram_total_mib": self.gpu.total_memory_mib(),
            "cpu_system": psutil.cpu_percent(percpu=True),
            "processes": len(processes), "threads": threads,
            "pids": observed, "notes": sorted(notes), "gpu_note": self.gpu.note,
            "alive": bool(processes),
        }
        if first:
            result["cpu_system"] = []
        self.previous, self.previous_wall = now, wall
        return result

    def close(self):
        self.gpu.close()
