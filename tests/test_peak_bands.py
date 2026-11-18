import csv
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

from chequebo.peaks import peak_context
from chequebo.session import SessionWriter, export_maxima, maximum
from chequebo.ui import Window
from test_chart_controls import javascript, rendered, sample
from test_process_history import row
from test_system import until


def history():
    return [{**sample(t), 'ram_mib': (30 if t == 4 else 12)*1024,
             'swap_mib': (20 if t == 5 else 0)*1024,
             'cpu_percent': 90 if t in (3, 7) else 20,
             'vram_mib': (6 if t == 6 else 2)*1024,
             'read_mib_s': 100 if t == 2 else 0, 'write_mib_s': 80 if t == 8 else 0,
             'net_recv_mbps': 50 if t == 9 else 0, 'net_sent_mbps': 30 if t == 10 else 0,
             'process_snapshot': [row(command=f'python step{t}.py', cpu=80 if t == 3 else 10)],
             'phases': [{'pid':42, 'thread':1, 'name':f'phase {t}', 'state':'running', 'start':1}]}
            for t in range(1, 21)]


def test_peak_bands_original_samples_units_ties_and_live_zoom(tmp_path, app):
    w = Window(tmp_path, auto_start=False)
    try:
        for s in history():
            w.add_sample(s)
        w.show(); rendered(w, app, 20)
        charts = javascript(w, 'window.Chequebo.state().latest.charts', app)
        peaks = {p['metric']: p for c in charts for p in c['peaks']}
        assert {k:p['elapsed'] for k,p in peaks.items()} == {
            'memory_mib':5, 'cpu_percent':3, 'gpu_percent':1, 'vram_mib':6,
            'read_mib_s':2, 'write_mib_s':8, 'net_recv_mbps':9, 'net_sent_mbps':10}
        assert peaks['memory_mib']['value'] == 32  # Combined simultaneous maximum, not sum of separate peaks.
        assert peaks['vram_mib']['value'] == 6
        shapes = javascript(w, "document.querySelector('[data-key=cpu_percent] .plot').layout.shapes", app)
        band = next(s for s in shapes if s.get('name') == 'peak:cpu_percent')
        assert band['yref'] == 'paper' and (band['y0'],band['y1']) == (0,1)
        assert band['layer'] == 'below' and band['xsizemode'] == 'pixel' and band['xanchor'] == 3
        w.zoom_chart('cpu_percent', 0, .25)
        w.zoom_chart('cpu_percent', 1, .5)
        yrange = w.chart_ranges['cpu_percent'][1]
        w.add_sample({**sample(21),'cpu_percent':30})
        rendered(w,app,21)
        assert w.chart_ranges['cpu_percent'][1] == yrange
        # An off-screen peak stays available by button without a misleading band at the boundary.
        assert javascript(w,"document.querySelector('[data-key=cpu_percent] .plot').layout.shapes.filter(s=>s.name==='peak:cpu_percent').length",app) == 0
        javascript(w,"document.querySelector('button[data-metric=cpu_percent]').click()",app)
        until(lambda: w.inspector.sample['elapsed'] == 3,app)
        assert w.bounds()[1] == 21 and w.bounds()[0] <= 3
        assert 'step3.py' in w.inspector.peak_details.toPlainText()
        assert 'phase 3' in w.inspector.peak_details.toPlainText()
        assert 'phase 7' not in w.inspector.peak_details.toPlainText()
        # The ten-minute buffer drops its old maximum instead of retaining expired context.
        w.add_sample({**sample(700),'cpu_percent':10})
        rendered(w,app,700)
        assert javascript(w,"window.Chequebo.state().latest.charts.find(c=>c.key==='cpu_percent').peaks[0].elapsed",app) == 700
    finally:
        w.close()


def test_actual_band_click_saved_context_and_range_table(tmp_path, app):
    path = tmp_path/'peaks.chequebo'
    writer = SessionWriter(path, {'scope':'system', 'cpu_count':8})
    for s in history(): writer.append(s)
    writer.close()
    w = Window(tmp_path, auto_start=False)
    try:
        w.load_recording(path); w.show(); rendered(w,app)
        # Click the RAM+swap band's empty area, away from every trace/diamond.
        coords = javascript(w,"""(()=>{const gd=document.querySelector('[data-key=memory_mib] .plot'), r=gd.getBoundingClientRect(), a=gd._fullLayout;return [r.left+a.xaxis._offset+a.xaxis.d2p(5),r.top+a.yaxis._offset+a.yaxis._length/2]})()""",app)
        QTest.mouseClick(w.dashboard.focusProxy(), Qt.MouseButton.LeftButton, pos=QPoint(*map(round,coords)))
        until(lambda: 'Recording peak' in w.inspector.peak_details.toPlainText(),app)
        assert w.inspector.sample['elapsed'] == 5
        assert 'RAM 12.000 GiB + swap 20.000 GiB' in w.inspector.peak_details.toPlainText()
        assert 'step5.py' in w.inspector.details.toPlainText()
        w.set_chart_time_range(6,10)
        cpu_row = next(i for i,m in enumerate(w.metrics) if m[0]=='cpu_percent')
        w.jump_to_peak(cpu_row,3)
        assert w.inspector.sample['elapsed'] == 7
        assert w.inspector.peak_details.toPlainText().startswith('Range peak')
        w.jump_to_peak(cpu_row,5)
        assert w.inspector.sample['elapsed'] == 3
        assert w.inspector.peak_details.toPlainText().startswith('Recording peak')
        assert 'step3.py' in w.table.item(cpu_row,5).text()
        export_maxima(tmp_path/'peaks.csv',w.samples,6,10,w.metadata)
        exported = {r['resource']:r for r in csv.DictReader((tmp_path/'peaks.csv').open())}
        cpu = exported['System CPU / total capacity']
        assert 'step3.py' in cpu['recording_peak_context'] and 'phase 3' in cpu['recording_peak_context']
        assert 'step7.py' in cpu['range_peak_context'] and cpu['range_peak_second'] == '7'
    finally:
        w.close()


def test_peak_context_has_no_invented_attribution():
    s = history()[0]
    assert 'not recorded' in peak_context(s,'gpu_percent')['summary']
    for key in ('read_mib_s','write_mib_s','net_recv_mbps','net_sent_mbps','swap_mib'):
        assert 'not recorded' in peak_context(s,key)['summary']
        assert 'step1.py' not in peak_context(s,key)['details']
    assert '512' not in peak_context(s,'gpu_percent')['details']  # VRAM is not evidence of GPU activity.
    s.pop('process_snapshot')
    assert 'not saved' in peak_context(s,'cpu_percent')['summary']
    s['process_snapshot'] = [dict(row(),cpu_percent=None)]
    assert 'No accessible' in peak_context(s,'cpu_percent')['summary']
    assert maximum([{'elapsed':1,'cpu_percent':None},{'elapsed':2,'cpu_percent':float('nan')}],'cpu_percent') is None


def test_peak_extrema_survive_decimation_and_missing_series(tmp_path, app):
    w = Window(tmp_path,auto_start=False)
    try:
        samples = [{**sample(t), 'cpu_percent':99 if t==1234 else 1,
                    'gpu_percent':None, 'net_recv_mbps':None, 'net_sent_mbps':None} for t in range(2200)]
        w.samples = samples
        w.viewing_live = False
        w.set_scope(); w.refresh(); w.show(); rendered(w,app)
        charts = javascript(w,'window.Chequebo.state().latest.charts',app)
        cpu = next(c for c in charts if c['key']=='cpu_percent')
        assert len(cpu['x']) < len(samples) and 1234 in cpu['x']
        assert cpu['peaks'][0]['elapsed'] == 1234 and cpu['peaks'][0]['value'] == 99
        assert not next(c for c in charts if c['key']=='gpu_percent')['peaks']
        assert not next(c for c in charts if c['key']=='net_recv_mbps')['peaks']
    finally:
        w.close()
