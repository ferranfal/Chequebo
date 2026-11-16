"""Muestreo sin privilegios. CPU y E/S son diferencias de contadores entre muestras."""

import os
import time
from pathlib import Path

import psutil

MIB = 1024 ** 2
GONE = (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError)


class ProcessTree:
    """Identifica cada proceso por PID + fecha de creación para evitar reutilizar PID.

    Al lanzar, también busca la sesión POSIX propia: conserva hijos huérfanos.
    Al adjuntar, solo puede descubrir hijos que existan durante una muestra.
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
    """GPU global + VRAM del árbol por NVIDIA; AMD global si expone gpu_busy_percent.

    No atribuimos el uso global de GPU al programa. Los porcentajes de varios
    dispositivos se presentan como su media, y la VRAM como suma entre tarjetas.
    """

    def __init__(self):
        self.nv = None
        self.handles = []
        self.amd = list(Path("/sys/class/drm").glob("card[0-9]*/device/gpu_busy_percent"))
        self.note = "GPU no disponible: controlador o permisos sin soporte."
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i)
                            for i in range(pynvml.nvmlDeviceGetCount())]
            if self.handles:
                self.note = "NVIDIA: actividad global; VRAM de los procesos observados."
        except (ImportError, RuntimeError, OSError) as exc:
            self.note = f"GPU no disponible: {exc}"
        except Exception as exc:
            # NVML tiene sus propias excepciones; GPU es una función opcional.
            self.note = f"GPU no disponible: {exc}"

    def sample(self, pids):
        if not self.handles:
            try:
                values = [int(path.read_text()) for path in self.amd]
                if values:
                    self.note = "AMD: actividad global; VRAM por proceso no disponible."
                return (sum(values) / len(values) if values else None), None
            except (OSError, ValueError):
                return None, None
        activity, memory = [], []
        valid_activity = valid_memory = True
        for handle in self.handles:
            try:
                activity.append(self.nv.nvmlDeviceGetUtilizationRates(handle).gpu)
            except self.nv.NVMLError:
                valid_activity = False
            records = {}
            for query in (self.nv.nvmlDeviceGetComputeRunningProcesses,
                          self.nv.nvmlDeviceGetGraphicsRunningProcesses):
                try:
                    for process in query(handle):
                        if process.pid in pids:
                            value = process.usedGpuMemory
                            # NVML representa "no disponible" como UINT64_MAX.
                            # Algunas versiones de pynvml no exportan la constante.
                            if value is None or value < 0 or value >= (1 << 64) - 1:
                                valid_memory = False
                            else:
                                records[process.pid] = max(records.get(process.pid, 0), value)
                except self.nv.NVMLError:
                    valid_memory = False
            memory.append(sum(records.values()))
        return (sum(activity) / len(activity) if activity and valid_activity else None,
                sum(memory) / MIB if valid_memory else None)

    def close(self):
        if self.nv:
            try:
                self.nv.nvmlShutdown()
            except self.nv.NVMLError:
                pass


class Sampler:
    def __init__(self, pid, own_session=False):
        self.tree = ProcessTree(pid, own_session)
        self.cpu_count = psutil.cpu_count() or 1
        self.started = self.previous = time.monotonic()
        self.previous_wall = time.time()
        self.counters = {}
        self.gpu = GPU()
        psutil.cpu_percent(percpu=True)  # Primera lectura: establece referencia.

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
                cpu_time = times.user + times.system  # No children_*: se contarían dos veces.
                threads += process.num_threads()
                rss += process.memory_info().rss
                observed.append(process.pid)
            except GONE:
                continue
            except psutil.AccessDenied:
                valid_cpu = valid_io = valid_ram = valid_swap = False
                notes.add("Hay procesos sin permiso de lectura; las métricas incompletas quedan vacías.")
                continue
            try:
                full = process.memory_full_info()
                ram += full.pss
                swap += full.swap
            except (psutil.Error, AttributeError):
                valid_ram = valid_swap = False
                notes.add("PSS/swap no disponibles para todo el árbol de procesos.")
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
            # Un hijo nuevo nació dentro del intervalo: sus contadores parten de cero.
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
