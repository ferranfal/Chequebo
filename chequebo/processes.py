"""Process snapshots. Identity includes creation time, never just a reusable PID."""

import shlex

import psutil

MIB = 1024 ** 2


class ProcessSampler:
    def __init__(self):
        self.previous = {}

    def sample(self, now, cpu_count, gpu_memory=None):
        current, rows = {}, []
        # psutil >= 6 can retain Process objects for reused PIDs between scans.
        # Fresh handles ensure creation times really describe this sample.
        if hasattr(psutil.process_iter, 'cache_clear'):
            psutil.process_iter.cache_clear()
        attributes = ['pid', 'create_time', 'name', 'ppid', 'username', 'cmdline',
                      'cpu_times', 'memory_info', 'status', 'num_threads']
        for process in psutil.process_iter(attributes, ad_value=None):
            try:
                info = process.info
                if info['create_time'] is None:
                    continue
                identity = f"{info['pid']}:{info['create_time']:.6f}"
                old = self.previous.get(identity)
                times, memory = info['cpu_times'], info['memory_info']
                cpu = times.user + times.system if times else None
                rss = memory.rss / MIB if memory else None
                metadata = dict(pid=info['pid'], created=info['create_time'], name=info['name'],
                                ppid=info['ppid'], user=info['username'], command=shlex.join(info['cmdline'] or []))
                if old and metadata == old['info']:
                    metadata = old['info']  # Share stable strings across live samples.
                dt = now - old['time'] if old else 0
                cores = (max(0, cpu - old['cpu']) / dt
                         if dt > 0 and cpu is not None and old['cpu'] is not None else None)
                delta = rss - old['rss'] if old and rss is not None and old['rss'] is not None else None
                rows.append(dict(identity=identity, info=metadata, rss_mib=rss, ram_delta_mib=delta,
                                 cpu_percent=100 * cores / max(1, cpu_count) if cores is not None else None,
                                 cpu_equivalent=cores, vram_mib=(gpu_memory or {}).get(info['pid']),
                                 threads=info['num_threads'], status=info['status']))
                current[identity] = dict(time=now, cpu=cpu, rss=rss, info=metadata)
            except (psutil.Error, OSError):
                continue  # A process may exit between any two reads.
        self.previous = current
        return rows
