"""Entrada de escritorio y grabación opcional desde terminal."""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Chequebo · Monitor de procesos para Linux")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "chequebo")
    parser.add_argument("--headless", action="store_true", help="Grabar sin abrir la ventana")
    parser.add_argument("--pid", type=int, help="PID al que adjuntarse")
    parser.add_argument("--interval", type=float, default=1.0, help="Segundos entre muestras (mínimo 0.2)")
    parser.add_argument("--output", type=Path, help="Archivo de sesión, en modo terminal")
    parser.add_argument("--check-gpu", action="store_true", help="Comprobar acceso a la GPU")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Comando tras --")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Esta versión de Chequebo está preparada para Linux.")
    if not 0.2 <= args.interval <= 60:
        parser.error("El intervalo debe estar entre 0.2 y 60 segundos.")
    if args.check_gpu:
        from .metrics import GPU
        gpu = GPU()
        try:
            activity, memory = gpu.sample({os.getpid()})
            print(json.dumps({"diagnostico": gpu.note, "actividad_global_porcentaje": activity,
                              "vram_de_este_proceso_mib": memory}, ensure_ascii=False, indent=2))
        finally:
            gpu.close()
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.pid and command:
        parser.error("Usa un PID o un comando, no ambos.")
    if args.headless:
        if not args.pid and not command:
            parser.error("Indica --pid o un comando tras --.")
        return record_headless(args, command)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .ui import Window
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Chequebo")
    app.setOrganizationName("Chequebo")
    window = Window(args.data_dir)
    window.interval.setValue(args.interval)
    window.show()
    if command:
        import shlex
        window.command.setText(shlex.join(command))
        QTimer.singleShot(0, window.start_command)
    elif args.pid:
        QTimer.singleShot(0, lambda: window.begin(args.pid, window.new_path(), f"PID {args.pid}"))
    return app.exec()


def record_headless(args, command):
    import shlex
    from PySide6.QtCore import QCoreApplication, QTimer
    from .recorder import Recorder, launch
    app = QCoreApplication([sys.argv[0]])
    args.data_dir.mkdir(parents=True, exist_ok=True)
    path = args.output or args.data_dir / (time.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6] + ".chequebo")
    if path.exists():
        raise SystemExit("El archivo de salida ya existe; elige otro para conservar la sesión.")
    path.parent.mkdir(parents=True, exist_ok=True)
    process = gate = None
    if command:
        process, gate = launch(command, Path.cwd(), path.with_suffix(".log"))
    recorder = Recorder(process.pid if process else args.pid, path, args.interval,
                        shlex.join(command) if command else f"PID {args.pid}", process, gate)
    result = {}

    def completed(session_path, metadata):
        result.update(metadata)
        print(json.dumps({"session": session_path, **metadata}, ensure_ascii=False), flush=True)

    recorder.completed.connect(completed)
    recorder.finished.connect(app.quit)
    signal.signal(signal.SIGINT, lambda *_: recorder.stop())
    signal.signal(signal.SIGTERM, lambda *_: recorder.stop())
    timer = QTimer()
    timer.timeout.connect(lambda: None)  # Permite entregar SIGINT al intérprete.
    timer.start(100)
    recorder.start()
    app.exec()
    recorder.wait()
    return 1 if result.get("state") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())

