"""Grabación en un hilo independiente; parar la grabación nunca mata el programa."""

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
    """Espera a que el muestreador esté preparado antes de ejecutar el comando.

    No usamos shell=True. Para tuberías el usuario puede pedir explícitamente
    bash -lc '...'. La sesión POSIX permite seguir hijos cuando termina el padre.
    """
    args = shlex.split(command) if isinstance(command, str) else list(command)
    if not args:
        raise ValueError("Introduce un comando.")
    read_fd, write_fd = os.pipe()
    gate = (
        "import os,sys; fd=int(sys.argv[1]); ready=os.read(fd,1); os.close(fd); "
        "sys.exit(0) if not ready else None; "
        "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
    )
    try:
        with open(log_path, "wb") as output:
            process = subprocess.Popen(
                [sys.executable, "-c", gate, str(read_fd), *args], cwd=cwd,
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
                sampler.sample()  # Referencia del proceso de arranque, sin guardarla.
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
                    self.process.poll()  # Recoge el padre aunque queden hijos vivos.
                sample = sampler.sample()
                if not sample["alive"]:
                    state = "finished"
                    break  # No inventamos una muestra final de ceros.
                store.append(sample)
                self.sample_ready.emit(sample)
                self.stop_requested.wait(max(0.0, self.interval - (time.monotonic() - tick)))
        except Exception as exc:
            state, error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            if self.gate is not None:
                os.close(self.gate)  # EOF cancela únicamente el arranque aún pendiente.
                self.gate = None
            if sampler:
                sampler.close()
            metadata.update(state=state, error=error, ended_at=time.time(),
                            exit_code=self.process.poll() if self.process else None)
            if store:
                try:
                    store.set_meta(metadata)
                except Exception as exc:
                    metadata.update(state="error", error=f"No se pudo cerrar la sesión: {exc}")
                finally:
                    store.close()
            self.completed.emit(str(self.path), metadata)

