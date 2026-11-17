"""Desktop entry point and optional headless recording."""

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Chequebo · Linux system monitor")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path,
                        help="Recording folder for this launch (overrides the saved folder)")
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
            activity, memory = gpu.sample()
            print(json.dumps({"diagnostic": gpu.note, "whole_system_activity_percent": activity,
                              "whole_system_vram_mib": memory}, ensure_ascii=False, indent=2))
        finally:
            gpu.close()
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.pid and command:
        parser.error("Use either a PID or a command, not both.")
    if args.headless:
        return record_headless(args, command)
    if args.pid or command:
        parser.error("For optional per-process recording, add --headless. The desktop monitors the whole computer.")
    from PyQt6.QtCore import QLocale
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication
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
    return app.exec()


def record_headless(args, command):
    import shlex
    from PyQt6.QtCore import QCoreApplication, QTimer
    from .recorder import Recorder, launch
    from .monitor import SystemMonitor
    from .preferences import recording_directory
    app = QCoreApplication([sys.argv[0]])
    args.data_dir = recording_directory(args.data_dir)
    path = args.output or args.data_dir / (time.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6] + ".chequebo")
    if path.exists():
        raise SystemExit("The output file already exists; choose another path to preserve the recording.")
    path.parent.mkdir(parents=True, exist_ok=True)
    process = gate = None
    if command:
        process, gate = launch(command, Path.cwd(), path.with_suffix(".log"))
    if process or args.pid:
        recorder = Recorder(process.pid if process else args.pid, path, args.interval,
                            shlex.join(command) if command else f"PID {args.pid}", process, gate)
    else:
        recorder = SystemMonitor(args.interval)
        recorder.start_recording(path)
    result = {}

    def completed(session_path, metadata):
        if metadata.get("state") == "recording":
            return
        result.update(metadata)
        print(json.dumps({"session": session_path, **metadata}, ensure_ascii=False), flush=True)
        if metadata.get("state") == "error" and isinstance(recorder, SystemMonitor):
            recorder.stop()

    if isinstance(recorder, SystemMonitor):
        recorder.recording_changed.connect(completed)
        recorder.failed.connect(lambda error: result.update(state="error", error=error))
    else:
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
