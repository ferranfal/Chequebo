"""Optional Python phase markers. Standard library only; never block an algorithm.

    from chequebo.trace import phase
    with phase('hstack'):
        result = numpy.hstack(parts)

Markers are sent locally to open Chequebo monitors; nothing is written by this
module and no exception from the tracing transport escapes into the algorithm.
"""

import contextvars
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

_parent = contextvars.ContextVar('chequebo_phase', default=None)


def trace_directory():
    root = os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir()
    return Path(root) / f'chequebo-phases-{os.getuid()}'


def _send(event):
    try:
        data = json.dumps(event, ensure_ascii=True).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.setblocking(False)
            for path in trace_directory().glob('*.sock'):
                try:
                    client.sendto(data, str(path))
                except OSError:
                    pass
    except (OSError, ValueError):
        pass


@contextmanager
def phase(name):
    event = dict(id=uuid4().hex, parent=_parent.get(), name=str(name)[:240],
                 pid=os.getpid(), thread=threading.get_native_id(), start=time.time(), end=None)
    token = _parent.set(event['id'])
    _send(event)
    try:
        yield
    finally:
        _send({**event, 'end': time.time()})
        _parent.reset(token)
