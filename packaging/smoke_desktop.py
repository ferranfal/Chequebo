"""Check the installed Ubuntu GUI actually renders Plotly and receives samples."""
import sys
import time

sys.path.insert(0, '/usr/lib/chequebo')
from PyQt6.QtWidgets import QApplication
from chequebo.ui import Window

app=QApplication(['chequebo-package-test'])
window=Window('/tmp/chequebo-package-test')
window.interval.setValue(.2)
window.show()
try:
    deadline=time.monotonic()+20
    state=[]
    while time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.02)
        if window.dashboard.ready and len(window.samples)>=3:
            if not state:
                window.dashboard.page().runJavaScript('window.Chequebo.state()',state.append)
            elif state[-1] and not state[-1]['rendering'] and len(state[-1]['plots'])==6:
                break
            else:
                state.clear()
    assert state and state[-1] and len(state[-1]['plots'])==6, 'Plotly did not render all six charts'
    assert 'net_recv_mbps' in window.samples[-1]
    assert window.bounds()[1] == window.samples[-1]['elapsed']
    print('Installed desktop: six Plotly charts, live samples and network metrics OK')
finally:
    window.close()
    app.processEvents()
