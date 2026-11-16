# Chequebo

**An English-language Linux desktop system monitor.** Open it to see your whole
computer live. Press Record when you want to save a session and inspect it later. Free and open
source under the MIT license.

## Install on Ubuntu

The installer **`chequebo.deb` is included in the root of this repository**.
Clone the repository and install it:

```bash
git clone https://github.com/ferranfal/Chequebo.git
cd Chequebo
sudo apt install ./chequebo.deb
```

If you already cloned it, run `git pull --ff-only` from the project folder first,
then `sudo apt install ./chequebo.deb`.

The package targets **Ubuntu 24.04 LTS, Intel/AMD 64-bit (amd64)**. It bundles the
application, Python runtime and Qt interface; APT installs the required system
libraries. Other Ubuntu versions and ARM packages are not tested yet.

You can also download the versioned installer from
[GitHub Releases](https://github.com/ferranfal/Chequebo/releases/latest).
The bundled `.deb` contains the latest published release, which can differ from
unreleased source changes on `main`. Its checksum is in `chequebo.deb.sha256`.
To check the bundled file before installation:

```bash
sha256sum --check chequebo.deb.sha256
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

1. Open **Chequebo** from Ubuntu's applications menu. CPU, RAM, disk and supported
   GPU measurements start automatically. There is no program or PID to enter.
2. The live panel keeps the **last 10 minutes** in memory without writing a
   recording to disk. Change the time range or click a chart to inspect history.
   Enable **Follow latest** to return the cursor to the newest sample.
3. Press **Record** to start saving the whole computer from that moment onward.
   Press **Stop recording** to save the session. **Live monitoring continues.**
4. Click **Open last recording** to inspect its complete history and peak table,
   including samples older than the live buffer. **Open recording** opens any
   earlier session. **Back to live** returns to the current computer activity.
   Monitoring and any active recording continue while you inspect a saved file.
5. The peak table covers the live buffer or the opened recording, as its heading
   indicates. It also shows peaks within the selected range. Click a peak to
   navigate to its time; export a peak report or samples to CSV.

Minimizing keeps monitoring and recording running. Closing the app finishes an
active recording and stops monitoring. It does not install a background service
or start automatically at login. Recordings go to `~/.local/share/chequebo`
(respecting `XDG_DATA_HOME`). Each SQLite sample is committed immediately. Earlier
committed samples can be recovered after an unexpected shutdown.

Recording saves only the period after pressing Record; the preceding live buffer
is not added. The first recorded CPU/disk rate is unavailable because its interval
would include time before Record. Live history is bounded to ten minutes; saved
recordings keep the full session on disk. Opening a saved file loads it into
memory, so very large recordings may take time and substantial RAM to explore.

## Understanding the measurements

| Metric | Scope and meaning |
|---|---|
| System CPU | Mean activity across logical CPUs. 100% means all logical CPUs are fully occupied. |
| CPU equivalents | 1.0 equals the work of one logical CPU at 100%. It is an equivalent amount of work, not a count of specific busy physical cores. |
| Each logical CPU | One bar per logical CPU (hardware thread), each from 0 to 100%, at the selected time. |
| System RAM | Total RAM minus available RAM, including memory used by the OS and all programs. Reclaimable memory remains available. |
| Swap | Used system swap. |
| RAM + swap | RAM usage plus swap usage at the same instant, displayed in GiB. |
| Disk I/O | System disk bytes read/written per second. Cached reads can produce zero physical reads. Layered storage can report activity at multiple device levels. |
| GPU | Whole-device activity, averaged across detected supported cards. |
| VRAM | Whole-device used GPU memory, summed across detected supported cards. |

On a machine with 8 logical CPUs, **50% system CPU = 4.0 CPU equivalents**. This
could be eight CPUs partly busy, four fully busy, or another distribution. The
individual bars show that distribution. Core and logical CPU counts are detected
automatically, including when a recording was made on a different machine.

The memory chart uses **GiB** and a fixed vertical range from zero to **physical
RAM + configured swap**. A **red horizontal line** marks the physical RAM limit.
For example, 64 GiB of RAM and 100 GiB of swap produce a 0–164 GiB axis with the
line at 64 GiB. The blue curve shows combined usage; the caption separates RAM
and swap usage, since Linux can use swap even while RAM remains available.
Changing configured swap updates the live scale automatically on the next sample.
Swap adds virtual memory capacity; it is not extra physical RAM.

New recordings preserve memory capacities. When viewing a recording, the scale
uses that computer's RAM and the largest swap capacity configured during the
recording. Older recordings without capacity data still open, with the missing
total identified instead of substituting the current computer's capacity.
Memory peaks in the table and peak report use GiB; raw sample CSV columns ending
in `_mib` keep their original MiB units. The combined peak is the largest
simultaneous RAM + swap usage, not the sum of their separate peaks.

Recordings made with older Chequebo versions still open and keep their original
program-specific labels: RAM PSS and NVIDIA VRAM attributed to observed processes,
with global GPU activity. They are not relabeled as whole-computer measurements.

## NVIDIA and other GPUs

**Check GPU** reports driver access, detected card memory and whole-system VRAM
usage. It also retries the monitor's GPU connection.

```bash
chequebo --check-gpu
nvidia-smi
```

NVIDIA uses NVML through `nvidia-ml-py`, included in the Ubuntu package. It requires
a working NVIDIA driver and access to the GPU; PyTorch and the CUDA Toolkit are
not needed. AMD uses `gpu_busy_percent` and `mem_info_vram_used` when the driver
exposes them in sysfs. Intel GPU activity is not implemented. Missing measurements
appear as **Unavailable**, never as zero. On mixed GPU systems, the NVIDIA adapter
is used when NVIDIA is detected; otherwise the AMD adapter is used.

Sampling intervals range from 0.2 to 60 seconds, defaulting to 1 second. Peaks are
**observed** maxima; spikes between samples can be missed. CPU and disk rates are
averages over the sampling interval. Network traffic and power are not measured.

## Headless recording (optional)

Record the whole computer without a window; Ctrl+C stops and saves:

```bash
chequebo --headless --output my-computer.chequebo
```

For advanced use, command-line process recording remains available:

```bash
chequebo --headless --interval 0.5 -- python3 my_program.py
chequebo --headless --pid 1234
```

This mode tracks the program and observed children. It does not terminate them
when recording stops. Very short children or work delegated to external services
may escape sampling; access restrictions can limit measurements. Commands run
with standard input closed, and output goes to a `.log` beside the recording.
For pipes or redirection, explicitly use `bash -lc 'your command'`.

`--data-dir /path` changes the recording folder. Source installations can
substitute `./run.sh` for `chequebo`.

## Development and tests

Small modules, no external service:

- `metrics.py`: system and optional process CPU, memory, disk and GPU adapters.
- `monitor.py`: continuous system worker and independent recording controls.
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
normalization with 1, 4, 8, 16, 64 and 128 logical CPUs, continuous system
monitoring, repeated recording sessions, bounded live history, memory units,
RAM/swap capacity changes and saved memory scales. Linux CI runs on Python
3.10, 3.12 and 3.13. Individual GPU drivers need real-hardware validation.

The Qt dependency currently uses the tested 6.11 series. A native reference-count
crash was reproduced with PySide6 6.12 on Python 3.10 when rendering live charts;
the upper bound should only be raised after checking that combination again.

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
and its SHA-256 checksum automatically. After a successful release build, the
**Bundle Ubuntu installer in repository** workflow verifies the release checksum
and updates `chequebo.deb` on `main`, so a normal clone includes the installer.

Chequebo code: [MIT](LICENSE). Bundled dependencies retain their own licenses,
included under `/usr/share/doc/chequebo/third-party` in the Ubuntu package. Qt
libraries remain separate shared libraries in the bundle.
