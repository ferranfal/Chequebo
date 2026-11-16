# Chequebo

**An English-language Linux desktop process monitor.** Launch a program, record
its resource usage and come back later to inspect what happened. Free and open
source under the MIT license.

## Install on Ubuntu

Download `chequebo_0.2.1_amd64.deb` from
[GitHub Releases](https://github.com/ferranfal/Chequebo/releases/latest).
The package targets **Ubuntu 24.04 LTS, Intel/AMD 64-bit (amd64)**. It bundles the
application, Python runtime and Qt interface; APT installs the required system
libraries. Other Ubuntu versions and ARM packages are not tested yet.

From the folder containing the downloaded file:

```bash
sudo apt install ./chequebo_0.2.1_amd64.deb
```

Open **Chequebo** from Ubuntu's applications menu, or run `chequebo` in a
terminal. No browser, server, Python environment setup or root access is needed
to run it. On desktops with a `.deb` package installer, opening the downloaded
file also starts installation. To remove the application:

```bash
sudo apt remove chequebo
```

Removing the package leaves your recordings in your user data directory.

## Run from source on Linux

Requires a Linux desktop and Python 3.10 or newer.

```bash
git clone https://github.com/ferranfal/Chequebo.git
cd Chequebo
./install.sh
./run.sh
```

The source installer uses `uv` when available, otherwise `venv` and `pip`. It
installs dependencies into `.venv` inside the project without changing system
Python. On Ubuntu, install `python3-venv` if needed. If `libxcb-cursor0` is missing
on X11, the script downloads it using APT and extracts it into `.venv/native`
without sudo; `run.sh` loads it only for Chequebo. Other missing Qt system
libraries can be installed with your distribution's package manager.

You can also use your own environment: `pip install '.[nvidia]'`, then `chequebo`.
Without the NVIDIA extra, CPU, RAM, disk and recording features still work.

## Use

1. Enter a command, select its working directory and click **Launch and record**.
   The source includes an example: `python3 examples/workload.py`.
2. For a running program, click **Select process and record**.
3. Choose the full recording, recent minutes or a custom range in seconds since
   the start. Click a chart or move the slider to inspect a particular time.
4. The table shows **peaks for the entire recording** alongside peaks within the
   visible range. Click a peak to find its time in the charts.
5. Export samples or a peak report to CSV. **Open recording** restores a previous
   recording even after its program has finished.

**Stopping a recording or closing the window does not terminate the monitored
program.** Closing stops collection; minimizing keeps it running. Recordings go
to `~/.local/share/chequebo` (respecting `XDG_DATA_HOME`). SQLite commits every
sample so an unexpected shutdown preserves earlier samples. Command output goes
to the `.log` beside its recording.

Commands run directly with standard input closed. For pipes or redirection,
explicitly use `bash -lc 'your command'`. This version targets noninteractive jobs.

## Understanding the measurements

| Metric | Scope and meaning |
|---|---|
| Program CPU | Sum of observed process CPU time, divided by elapsed time and the machine's logical CPU count. 100% means all logical CPUs are fully occupied. |
| Logical CPUs worth of work | Secondary reference: 1.0 is the work of one logical CPU running at 100%. It does not count physical cores or identify particular CPUs. |
| Each logical CPU | **Whole-system** activity at the selected time, including other programs. |
| RAM PSS | Physical memory of observed processes with shared pages apportioned. Avoids counting every shared page as private memory. |
| Swap | Sum of Linux-reported swap for observed processes. |
| Disk I/O | Physical bytes read/written per second from Linux counters. Cached reads can produce zero physical reads. |
| GPU | Whole-device activity, averaged across cards. This percentage is not attributed to the program. |
| VRAM | NVIDIA memory attributed to observed PIDs, deduplicated across graphics/compute lists and summed across cards. |

For example, on a machine with 8 logical CPUs, a program fully occupying one
logical CPU shows approximately **12.5% program CPU** and **1.0 logical CPUs worth
of work**. On a 16-logical-CPU machine the same work is approximately 6.25%.
The operating system can move that work between CPUs, so this does not mean the
program stayed on one particular core. The bottom-right bars show the whole
computer's activity, with each bar scaled from 0 to 100%.

The number of logical CPUs is **detected automatically**. No fixed core count is
hardcoded in the interface. Recordings retain their original machine's topology
and can be opened on another machine.

## NVIDIA and other GPUs

**Check GPU** reports driver access and detected card memory. It also retries
GPU access during an active recording.

```bash
chequebo --check-gpu
nvidia-smi
```

For a source checkout, use `./run.sh --check-gpu` instead. NVIDIA uses NVML through
`nvidia-ml-py`, included in the Ubuntu package. It requires a working driver and
access to `/dev/nvidia*`; PyTorch and the CUDA Toolkit are not required. AMD global
activity is read when the driver exposes `gpu_busy_percent` in sysfs. AMD/Intel
per-process VRAM and Intel activity are not implemented. Missing values appear
as **Unavailable**, never zero. A valid zero VRAM reading can mean the program
is not using the GPU.

## Limits

- Sampling intervals range from 0.2 to 60 seconds, defaulting to 1 second. Peaks
  are the largest **observed** values; spikes between samples can be missed.
  CPU and disk rates are averages between reads.
- Observed children are tracked using PID + creation time. Launched programs
  have their own POSIX session so orphan children in that session can be found.
  Very short children, children detaching before discovery and work delegated
  to external services may escape. No cgroups, eBPF or privileged service is used.
- History before recording cannot be recovered. Very short runs may yield no
  samples. The first rate read establishes a baseline.
- Other users' processes and `/proc` restrictions can limit readings. The GUI
  process picker lists your own user's processes.
- Per-process network and energy are not measured. Per-logical-CPU activity and
  global GPU activity are not attributed to a particular program.
- Samples stay in memory for exploration. This version is designed for
  individual workloads, not months of continuous monitoring.

## Headless recording

```bash
chequebo --headless --interval 0.5 -- python3 my_program.py
chequebo --headless --pid 1234
chequebo --headless --output my-recording.chequebo -- ./my-program
```

Ctrl+C stops collection without killing the program. Open the file in the desktop
app later. `--data-dir /path` changes the recording folder. Source installations
can substitute `./run.sh` for `chequebo`.

## Development and tests

Small modules, no external service:

- `metrics.py`: process tree, CPU, memory, disk and GPU adapters.
- `session.py`: SQLite, peak calculations and CSV export.
- `recorder.py`: program launch and sampling on a worker thread.
- `ui.py`: desktop window and chart navigation.
- `__main__.py`: desktop and headless entry points.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,nvidia]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Tests cover peaks, missing values, persistence, real processes, orphan children,
stopping recordings, VRAM deduplication, chart navigation, packaged launch behavior
and normalization with 1, 4, 8, 16, 64 and 128 logical CPUs. Linux CI runs on Python
3.10, 3.12 and 3.13. Individual GPU drivers need real-hardware validation.

### Build the Ubuntu package

Build on Ubuntu 24.04 amd64 with a clean virtual environment:

```bash
sudo apt install python3-venv libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0
python3 -m venv .build-venv
.build-venv/bin/python -m pip install -e '.[nvidia,build]'
CHEQUEBO_BUILD_PYTHON="$PWD/.build-venv/bin/python" ./packaging/build-deb.sh
```

The `.deb` is written to `dist/`. The package contains a standalone directory
under `/opt/chequebo`, a `chequebo` command, an application-menu entry and an icon.
Building requires internet access to install dependencies; running does not.
Published GitHub releases trigger a clean Ubuntu build and attach the installer
and its SHA-256 checksum automatically.

Chequebo code: [MIT](LICENSE). Bundled dependencies retain their own licenses,
included under `/usr/share/doc/chequebo/third-party` in the Ubuntu package. Qt
libraries remain separate shared libraries in the bundle.
