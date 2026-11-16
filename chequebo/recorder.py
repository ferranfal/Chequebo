"""Recording on a worker thread; stopping never kills the monitored program."""

import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QThread, Signal

from .metrics import GPU, Sampler
from .session import SessionWriter


def launch(command, cwd, log_path):
    """Wait for the sampler to be ready before executing the command.

    No shell=True. Users can explicitly request bash -lc '...' for pipelines.
    The POSIX session allows tracking children after the parent exits.
    """
    args = shlex.split(command) if isinstance(command, str) else list(command)
    if not args:
        raise ValueError("Enter a command.")
    read_fd, write_fd = os.pipe()
    gate = (
        "import os,sys; fd=int(sys.argv[1]); ready=os.read(fd,1); os.close(fd); "
        "sys.exit(0) if not ready else None; "
        "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
    )
    try:
        environment = os.environ.copy()
        interpreter = sys.executable
        if getattr(sys, "frozen", False):
            # A frozen executable is not a Python interpreter. Do not leak its
            # private shared libraries into the user's program either.
            interpreter = "/usr/bin/python3"
            original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
            if original is None:
                environment.pop("LD_LIBRARY_PATH", None)
            else:
                environment["LD_LIBRARY_PATH"] = original
            bundle = str(getattr(sys, "_MEIPASS", ""))
            for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
                if bundle and environment.get(key, "").startswith(bundle):
                    environment.pop(key)
        with open(log_path, "wb") as output:
            process = subprocess.Popen(
                [interpreter, "-c", gate, str(read_fd), *args], cwd=cwd, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(read_fd,),
            )
    except Exception:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return process, write_fd


class Recorder(QThread):
    sample_ready = Signal(dict)
    completed = Signal(str, dict)

    def __init__(self, pid, path, interval=1.0, command="", process=None, gate=None, parent=None):
        super().__init__(parent)
        self.pid, self.path, self.interval = pid, Path(path), interval
        self.command, self.process, self.gate = command, process, gate
        self.stop_requested = threading.Event()
        self.retry_gpu = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        sampler = store = None
        metadata = {
            "command": self.command, "pid": self.pid, "interval": self.interval,
            "cpu_count": psutil.cpu_count() or 1,
            "physical_cores": psutil.cpu_count(logical=False),
            "started_at": time.time(), "state": "recording",
        }
        state, error = "stopped", ""
        try:
            store = SessionWriter(self.path, metadata)
            sampler = Sampler(self.pid, own_session=self.process is not None)
            if self.gate is not None:
                sampler.sample()  # Baseline for the launcher process, without saving it.
                sampler.started = time.monotonic()
                os.write(self.gate, b"1")
                os.close(self.gate)
                self.gate = None
            while not self.stop_requested.is_set():
                tick = time.monotonic()
                if self.retry_gpu.is_set():
                    sampler.gpu.close()
                    sampler.gpu = GPU()
                    self.retry_gpu.clear()
                if self.process:
                    self.process.poll()  # Reap the parent even while children are alive.
                sample = sampler.sample()
                if not sample["alive"]:
                    state = "finished"
                    break  # Do not invent a final sample of zeros.
                store.append(sample)
                self.sample_ready.emit(sample)
                self.stop_requested.wait(max(0.0, self.interval - (time.monotonic() - tick)))
        except Exception as exc:
            state, error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            if self.gate is not None:
                os.close(self.gate)  # EOF only cancels a launch that is still pending.
                self.gate = None
            if sampler:
                sampler.close()
            metadata.update(state=state, error=error, ended_at=time.time(),
                            exit_code=self.process.poll() if self.process else None)
            if store:
                try:
                    store.set_meta(metadata)
                except Exception as exc:
                    metadata.update(state="error", error=f"Unable to finalize the recording: {exc}")
                finally:
                    store.close()
            self.completed.emit(str(self.path), metadata)
