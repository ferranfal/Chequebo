import time

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest

from chequebo.session import SessionWriter
from chequebo.ui import Window
from test_system import FakeSampler, until


def sample(elapsed):
    return {**FakeSampler().sample(), 'elapsed': elapsed, 'ram_mib': 10*1024,
            'ram_total_mib': 64*1024, 'swap_total_mib': 100*1024,
            'vram_mib': 2*1024, 'vram_total_mib': 8*1024}


def javascript(window, source, app):
    result = []
    window.dashboard.page().runJavaScript(source, result.append)
    until(lambda: bool(result), app, 10)
    return result[0]


def rendered(window, app, expected=None):
    until(lambda: window.dashboard.ready, app, 10)
    def done():
        state = javascript(window, 'window.Chequebo.state()', app)
        return (state and not state['rendering'] and len(state['plots']) == 6 and
                (expected is None or state['plots']['cpu_percent']['x'][1] == expected))
    until(done, app, 10)


def test_zoom_and_selection_keep_latest_sample_at_right(tmp_path, app):
    w = Window(tmp_path, auto_start=False)
    try:
        for t in range(1, 21): w.add_sample(sample(t))
        w.select_time(3)
        w.zoom_chart('cpu_percent', 0, .5)
        width = w.live_window_seconds
        w.zoom_chart('memory_mib', 1, .5)
        yrange = w.chart_ranges['memory_mib'][1]
        w.add_sample(sample(21))
        assert w.bounds() == pytest.approx((21-width, 21))
        assert w.chart_ranges['memory_mib'][1] == yrange
        assert w.cursor.value() == 2
        assert w.follow.isChecked()
        # Dragging a live chart into the past changes width, never freezes time.
        w.chart_event({'type':'range', 'key':'cpu_percent', 'x':[2,7], 'y':None})
        w.add_sample(sample(22))
        assert w.bounds() == (17,22)
        w.follow.setChecked(False)
        assert w.follow.isChecked()
        w.reset_chart('memory_mib')
        assert w.chart_ranges['memory_mib'][1] == [0,164]
        assert w.bounds()[1] == 22
    finally:
        w.close()


def test_plotly_wheel_requires_click_and_preserves_live_edge(tmp_path, app, monkeypatch):
    w = Window(tmp_path, auto_start=False)
    try:
        for t in range(1,31): w.add_sample(sample(t))
        w.show()
        rendered(w, app, 30)
        # Send real Qt wheel events to the Chromium render widget.
        target = w.dashboard.focusProxy()
        point = QPoint(280,210)
        def wheel():
            event=QWheelEvent(QPointF(point),QPointF(target.mapToGlobal(point)),QPoint(),QPoint(0,-120),
                              Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
            app.sendEvent(target,event)
        QTest.mouseMove(target, point)
        before = w.bounds()
        wheel()
        until(lambda: w.scroll.verticalScrollBar().value() > 0, app)
        assert w.bounds() == before
        w.scroll.verticalScrollBar().setValue(0)
        QTest.mouseClick(target,Qt.MouseButton.LeftButton,pos=point)
        until(lambda: javascript(w,'window.Chequebo.state().active',app) == 'memory_mib', app)
        wheel()
        until(lambda: 'memory_mib' in w.manual_y, app)
        assert w.scroll.verticalScrollBar().value() == 0
        w.add_sample(sample(31))
        rendered(w, app, 31)
        assert javascript(w,"window.Chequebo.state().plots.memory_mib.x[1]",app) == 31
        assert w.follow.isChecked()
        QTest.keyClick(target,Qt.Key.Key_Escape)
        until(lambda: javascript(w,'window.Chequebo.state().active',app) is None, app)
        wheel()
        until(lambda: w.scroll.verticalScrollBar().value() > 0, app)
        assert javascript(w,"document.getElementById('error').hidden",app)
        png = tmp_path / 'chart.png'
        monkeypatch.setattr('chequebo.charts.QFileDialog.getSaveFileName', lambda *args: (str(png), 'PNG'))
        javascript(w,"document.querySelector('[data-key=memory_mib] [data-title=\"Download plot as a PNG\"]').click()",app)
        until(lambda: png.exists() and png.stat().st_size > 100, app, 10)
        assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    finally:
        w.close()


def test_plotly_toolbar_fullscreen_and_recording_continue(tmp_path, app, monkeypatch):
    monkeypatch.setattr('chequebo.monitor.SystemSampler',FakeSampler)
    w=Window(tmp_path)
    w.interval.setValue(.2)
    w.show()
    try:
        until(lambda: len(w.samples)>=2,app)
        rendered(w,app)
        w.start_button.click()
        until(lambda: w.recording,app)
        assert w.chart_markers[0] is not None and w.chart_markers[1] is None
        javascript(w,"document.querySelector('[data-key=cpu_percent] [data-title=\"Zoom X in\"]').click()",app)
        until(lambda: w.live_window_seconds is not None,app)
        javascript(w,"document.querySelector('[data-key=cpu_percent] [data-title=\"Full screen / Restore\"]').click()",app)
        until(lambda: w.fullscreen_dialog is not None,app)
        assert w.fullscreen_dialog.isFullScreen()
        before = w.samples[-1]['elapsed']
        until(lambda: w.samples[-1]['elapsed'] > before+1,app)
        assert 'Recording' in w.fullscreen_status.text()
        assert w.bounds()[1] == w.samples[-1]['elapsed']
        QTest.keyClick(w.dashboard.focusProxy(),Qt.Key.Key_Escape)
        until(lambda: w.fullscreen_dialog is None,app)
        w.stop_button.click()
        until(lambda: w.last_recording is not None,app)
        assert w.chart_markers[1] > w.chart_markers[0]
        duration=w.recording_span['duration']
        w.load_recording(w.last_recording)
        assert w.chart_markers == [0,duration]
        assert not w.viewing_live and w.monitor.isRunning()
        w.set_chart_time_range(.1,.5)
        assert w.bounds() == [.1,.5]
        w.show_live()
        before=len(w.samples)
        until(lambda: len(w.samples)>before,app)
        assert w.bounds()[1] == w.samples[-1]['elapsed']
        w.start_button.click()
        until(lambda: w.recording,app)
        assert w.chart_markers[1] is None
        w.stop_button.click()
        until(lambda: not w.recording,app)
    finally:
        w.monitor.stop()
        assert w.monitor.wait(3000)
        app.processEvents()
        w.close()


def test_saved_gpu_capacity_and_network_legacy_are_preserved(tmp_path, app):
    path=tmp_path/'legacy.chequebo'
    writer=SessionWriter(path,{'scope':'system','cpu_count':8,'vram_total_mib':24*1024})
    saved=sample(1)
    saved['vram_total_mib']=24*1024
    saved.pop('net_recv_mbps'); saved.pop('net_sent_mbps')
    writer.append(saved); writer.close()
    w=Window(tmp_path,auto_start=False)
    try:
        w.add_sample(sample(2))
        w.load_recording(path)
        assert w.chart_ranges['vram_mib'][1] == [0,24]
        assert w.chart_values['vram_mib'] == '2.00 / 24.00 GiB used'
        assert 'Unavailable' in w.chart_values['net_recv_mbps']
        w.show_live()
        assert w.chart_ranges['vram_mib'][1] == [0,8]
    finally:
        w.close()
