"""Desktop entry point and optional headless recording."""

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
    parser = argparse.ArgumentParser(description="Chequebo · Linux process monitor")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "chequebo")
    parser.add_argument("--headless", action="store_true", help="Record without opening a window")
    parser.add_argument("--pid", type=int, help="Process ID to attach to")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between samples (minimum 0.2)")
    parser.add_argument("--output", type=Path, help="Output recording file in headless mode")
    parser.add_argument("--check-gpu", action="store_true", help="Check GPU access")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("This version of Chequebo supports Linux.")
    if not 0.2 <= args.interval <= 60:
        parser.error("The interval must be between 0.2 and 60 seconds.")
    if args.check_gpu:
        from .metrics import GPU
        gpu = GPU()
        try:
            activity, memory = gpu.sample({os.getpid()})
            print(json.dumps({"diagnostic": gpu.note, "whole_system_activity_percent": activity,
                              "this_process_vram_mib": memory}, ensure_ascii=False, indent=2))
        finally:
            gpu.close()
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.pid and command:
        parser.error("Use either a PID or a command, not both.")
    if args.headless:
        if not args.pid and not command:
            parser.error("Specify --pid or a command after --.")
        return record_headless(args, command)
    from PySide6.QtCore import QLocale, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from .ui import Window
    app = QApplication([sys.argv[0]])
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    app.setApplicationName("Chequebo")
    app.setOrganizationName("Chequebo")
    app.setDesktopFileName("io.github.ferranfal.Chequebo")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources/chequebo.svg")))
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
        raise SystemExit("The output file already exists; choose another path to preserve the recording.")
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
    timer.timeout.connect(lambda: None)  # Allow SIGINT delivery to the interpreter.
    timer.start(100)
    recorder.start()
    app.exec()
    recorder.wait()
    return 1 if result.get("state") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
