"""Linux-local phase receiver. Credential checks bind markers to their sender PID."""

import json
import math
import os
import socket
import stat
import struct
from uuid import uuid4

from .trace import trace_directory


class PhaseReceiver:
    def __init__(self):
        self.socket = None
        self.path = None
        self.active = {}
        self.note = ''
        try:
            directory = trace_directory()
            directory.mkdir(mode=0o700, exist_ok=True)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError('Phase directory must be private to the current user')
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.socket.setblocking(False)
            self.path = directory / f'{os.getpid()}-{uuid4().hex[:8]}.sock'
            self.socket.bind(str(self.path))
            os.chmod(self.path, 0o600)
        except OSError as exc:
            self.note = f'Python phase markers unavailable: {exc}'
            self.close()

    def sample(self, processes, timestamp):
        if self.socket is None:
            return []
        completed = []
        for _ in range(4096):  # Bound optional work so monitoring cannot be starved.
            try:
                data, ancillary, flags, _ = self.socket.recvmsg(4096, socket.CMSG_SPACE(12))
            except BlockingIOError:
                break
            except OSError:
                break
            credentials = next((struct.unpack('3i', value[:12]) for level, kind, value in ancillary
                                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) >= 12), None)
            try:
                event = json.loads(data)
                if flags & socket.MSG_TRUNC or not credentials or credentials[:2] != (event['pid'], os.getuid()):
                    continue
                if (not isinstance(event['id'], str) or len(event['id']) != 32 or
                        not isinstance(event['name'], str) or len(event['name']) > 240 or
                        not isinstance(event['thread'], int) or
                        not isinstance(event['start'], (int, float)) or not math.isfinite(event['start']) or
                        event['start'] > timestamp + 1):
                    continue
                end = event.get('end')
                if end is not None and (not isinstance(end, (int, float)) or not math.isfinite(end) or end < event['start'] or end > timestamp + 1):
                    continue
                event = {key: event.get(key) for key in ('id', 'parent', 'name', 'pid', 'thread', 'start', 'end')}
                key = (event['pid'], event['id'])
                if end is None:
                    if len(self.active) < 4096:
                        self.active[key] = event
                else:
                    self.active.pop(key, None)
                    completed.append({**event, 'state': 'finished'})
            except (ValueError, KeyError, TypeError):
                continue
        by_pid = {row['info']['pid']: row['info'] for row in processes}
        for key, event in list(self.active.items()):
            info = by_pid.get(event['pid'])
            if info and info['created'] <= event['start'] + .01:
                continue
            completed.append({**event, 'state': 'process ended or unavailable'})
            self.active.pop(key)
        return [dict(event, state='running') for event in self.active.values()] + completed

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None
        if self.path:
            self.path.unlink(missing_ok=True)
            self.path = None
