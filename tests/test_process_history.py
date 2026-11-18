import csv
import os
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace as NS

import psutil
import pytest

from chequebo.metrics import GPU
from chequebo.processes import ProcessSampler
from chequebo.phases import PhaseReceiver
from chequebo.trace import phase, _send
from chequebo.session import SessionWriter, load_session, export_processes
from chequebo.ui import Window
from chequebo.monitor import SystemMonitor
from test_system import until
from test_chart_controls import sample, rendered, javascript


def process(pid=42, created=100, cpu=1, rss=1024, **extra):
    return NS(info=dict(pid=pid, create_time=created, name='python', ppid=1, username='researcher',
                        cmdline=['python', 'algorithm.py', '--label', 'two words'],
                        cpu_times=NS(user=cpu, system=0), memory_info=NS(rss=rss * 1024**2),
                        status='running', num_threads=4, **extra))


def row(pid=42, name='python', command='python algorithm.py', rss=1024, cpu=25, created=100):
    return dict(identity=f'{pid}:{created:.6f}', info=dict(pid=pid, created=created, name=name,
                ppid=1 if pid != 1 else 0, user='researcher', command=command), rss_mib=rss,
                ram_delta_mib=32, cpu_percent=cpu, cpu_equivalent=2, vram_mib=512,
                status='running', threads=4)


def test_process_rates_pid_reuse_and_unavailable_values(monkeypatch):
    processes = [process()]
    monkeypatch.setattr('chequebo.processes.psutil.process_iter', lambda *a, **k: processes)
    sampler = ProcessSampler()
    first = sampler.sample(10, 8)[0]
    assert first['cpu_percent'] is None and first['ram_delta_mib'] is None
    processes[:] = [process(cpu=5, rss=2048)]
    second = sampler.sample(12, 8, {42: 600})[0]
    assert second['cpu_percent'] == 25 and second['cpu_equivalent'] == 2
    assert second['ram_delta_mib'] == 1024 and second['vram_mib'] == 600
    assert second['info'] is first['info']
    assert "'two words'" in second['info']['command']
    processes[:] = [process(created=200, cpu=1)]  # A different process reused the PID.
    third = sampler.sample(14, 8)[0]
    assert third['identity'] != first['identity'] and third['cpu_percent'] is None
    processes[0].info.update(cpu_times=None, memory_info=None, cmdline=None)
    fourth = sampler.sample(16, 8)[0]
    assert fourth['rss_mib'] is None and fourth['cpu_percent'] is None
    processes.clear()
    assert sampler.sample(18, 8) == [] and not sampler.previous


def test_nvidia_owners_deduplicate_graphics_and_compute():
    gpu = GPU.__new__(GPU)
    gpu.handles = [0, 1]
    gpu.nv = NS(NVMLError=RuntimeError,
                nvmlDeviceGetComputeRunningProcesses=lambda h: [NS(pid=42, usedGpuMemory=100*1024**2), NS(pid=9, usedGpuMemory=(1<<64)-1)],
                nvmlDeviceGetGraphicsRunningProcesses=lambda h: [NS(pid=42, usedGpuMemory=100*1024**2)])
    assert gpu.process_memory() == {42: 200}
    gpu.handles = []
    assert gpu.process_memory() == {}


def test_process_storage_is_compressed_and_preserves_command_changes(tmp_path):
    path = tmp_path/'history.chequebo'
    writer = SessionWriter(path, {'scope':'system'})
    before = {**sample(1), 'process_snapshot':[row()]}
    after = {**sample(2), 'process_snapshot':[row(command='python second.py', rss=2048)]}
    writer.append(before); writer.append(before); writer.append(after); writer.close()
    assert isinstance(before['process_snapshot'][0]['info'], dict)  # Writer never mutates live history.
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM process_info').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM process_samples').fetchone()[0] == 3
        assert 'process_snapshot' not in db.execute('SELECT payload FROM samples LIMIT 1').fetchone()[0]
    meta, saved = load_session(path)
    assert saved[0]['process_snapshot'][0]['info']['command'] == 'python algorithm.py'
    assert saved[-1]['process_snapshot'][0]['info']['command'] == 'python second.py'
    assert saved[0]['process_snapshot'][0]['info'] is saved[1]['process_snapshot'][0]['info']
    export_processes(tmp_path/'processes.csv', saved)
    exported = list(csv.DictReader((tmp_path/'processes.csv').open()))
    assert exported[-1]['rss_mib'] == '2048' and exported[-1]['command'] == 'python second.py'


def test_old_recording_without_process_tables_still_opens(tmp_path):
    path=tmp_path/'old.chequebo'
    writer=SessionWriter(path, {'scope':'system'}); writer.append(sample(1)); writer.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE process_info'); db.execute('DROP TABLE process_samples')
    assert 'process_snapshot' not in load_session(path)[1][0]


def test_chart_click_selects_historical_processes_and_dark_theme(tmp_path, app):
    w = Window(tmp_path, auto_start=False)
    first = {**sample(1), 'process_snapshot':[row(), row(pid=1, name='launcher', command='launcher job', rss=10)]}
    second = {**sample(2), 'process_snapshot':[row(pid=43, command='python other.py')]}
    path=tmp_path/'history.chequebo'
    writer=SessionWriter(path, {'scope':'system','cpu_count':8}); writer.append(first); writer.append(second); writer.close()
    try:
        w.add_sample(first); w.add_sample(second); w.show()
        rendered(w,app)
        assert javascript(w,"document.querySelector('.plot')._fullLayout.plot_bgcolor",app) == '#1c2531'
        javascript(w,"document.querySelector('.plot').emit('plotly_click',{points:[{x:1}]})",app)
        app.processEvents()
        assert w.inspector.sample is first
        assert 'launcher job' in w.inspector.details.toPlainText()
        w.add_sample({**sample(3), 'process_snapshot':[]})
        assert w.inspector.sample is first and w.bounds()[1] == 3
        w.load_recording(path); w.select_time(1)
        assert w.inspector.rows[0]['info']['command'] == 'python algorithm.py'
        w.inspector.sort_resource('ram_delta_mib')
        w.inspector.filter.setText('algorithm.py')
        assert len(w.inspector.rows) == 1
        w.inspector.filter.clear()
        w.follow_processes()
        assert w.inspector.sample['elapsed'] == 2
        w.show_live()
        assert w.inspector.sample['elapsed'] == 3
    finally:
        w.close()


def own_snapshot():
    return [row(pid=os.getpid(), created=psutil.Process().create_time())]


def test_nested_phases_survive_short_calls_exceptions_and_recording(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    receiver = PhaseReceiver()
    assert receiver.socket is not None, receiver.note
    try:
        with phase('transform'):
            active = receiver.sample(own_snapshot(), time.time())
            assert active[0]['name'] == 'transform' and active[0]['state'] == 'running'
            with pytest.raises(ValueError):
                with phase('hstack'):
                    raise ValueError('algorithm failure')
            events = receiver.sample(own_snapshot(), time.time())
            child = next(e for e in events if e['name']=='hstack')
            assert child['parent'] == active[0]['id'] and child['state'] == 'finished'
            assert child['end'] >= child['start']
        completed = receiver.sample(own_snapshot(), time.time())
        assert completed[0]['state'] == 'finished'
        writer=SessionWriter(tmp_path/'phases.chequebo', {'scope':'system'})
        writer.append({**sample(1), 'phases':events, 'process_snapshot':own_snapshot()}); writer.close()
        assert load_session(tmp_path/'phases.chequebo')[1][0]['phases'] == events
        # The sender PID is authenticated by the kernel, not trusted from JSON.
        _send({**child, 'id':'a'*32, 'pid':os.getpid()+1234, 'end':None})
        assert receiver.sample(own_snapshot(), time.time()) == []
    finally:
        socket_path=receiver.path
        receiver.close()
        assert not socket_path.exists()
    with phase('no monitor open'):
        pass


def test_real_algorithm_process_and_phases_are_recovered_after_exit(tmp_path, monkeypatch, app):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    monitor = SystemMonitor(.1)
    live, changes = [], []
    monitor.sample_ready.connect(live.append)
    monitor.recording_changed.connect(lambda path, meta: changes.append(meta))
    monitor.start()
    child = None
    try:
        until(lambda: bool(live), app, 10)
        path = tmp_path/'algorithm.chequebo'
        monitor.start_recording(path)
        until(lambda: bool(changes), app)
        script = ('import sys; from chequebo.trace import phase; sys.stdin.readline()\n'
                  'with phase("allocate matrix"):\n data=bytearray(20*1024*1024); sys.stdin.readline()\n')
        child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, text=True)
        until(lambda: any(r['info']['pid']==child.pid for s in live for r in s.get('process_snapshot', [])), app, 10)
        child.stdin.write('allocate\n'); child.stdin.flush()
        until(lambda: any(r['info']['pid']==child.pid and r['ram_delta_mib'] is not None and r['ram_delta_mib'] > 10
                          for s in live for r in s.get('process_snapshot', [])), app, 10)
        child.stdin.write('finish\n'); child.stdin.flush()
        until(lambda: child.poll() is not None, app, 10)
        until(lambda: any(e['name']=='allocate matrix' and e['state']=='finished' for s in live for e in s.get('phases', [])), app, 10)
        monitor.stop_recording()
        until(lambda: len(changes)==2, app)
        _, saved = load_session(path)
        rows = [r for s in saved for r in s.get('process_snapshot', []) if r['info']['pid']==child.pid]
        assert rows and all('allocate matrix' in r['info']['command'] for r in rows)
        assert max(r['rss_mib'] for r in rows) >= 20
        assert any(r['ram_delta_mib'] is not None and r['ram_delta_mib'] > 10 for r in rows)
        assert any(e['name']=='allocate matrix' and e['state']=='finished' for s in saved for e in s['phases'])
        assert all(r['cpu_percent'] is None for r in saved[0]['process_snapshot'])
    finally:
        if child and child.poll() is None:
            child.terminate(); child.wait(timeout=3)
        monitor.stop()
        assert monitor.wait(5000)
