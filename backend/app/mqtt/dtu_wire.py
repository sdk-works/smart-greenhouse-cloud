"""Bounded newline framing and checksummed DTU envelope. No credentials."""
import hashlib
import json
import re
import time

MAX_LINE = 8192
TIMEOUT = 20.0

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')

def pack(body):
    msg_id = body.get('msg_id')
    if not isinstance(msg_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', msg_id):
        raise ValueError('A stable msg_id is required')
    digest = hashlib.sha256(canonical(body)).hexdigest()
    envelope = dict(wire='dtu-v1', msg_id=msg_id, body=body, sha256=digest)
    data = canonical(envelope) + b'\n'
    if len(data) > MAX_LINE:
        raise ValueError('DTU message exceeds 8192 bytes; use separate image transport')
    return data, digest

def unpack(envelope):
    body = envelope.get('body')
    if envelope.get('wire') != 'dtu-v1' or not isinstance(body, dict):
        raise ValueError('Invalid DTU envelope')
    _, digest = pack(body)
    if envelope.get('msg_id') != body['msg_id'] or envelope.get('sha256') != digest:
        raise ValueError('DTU checksum or identity mismatch')
    return body, digest

class LineFramer:
    def __init__(self):
        self.buffer = bytearray()
        self.started = None
        self.dropping = False
        self.errors = 0

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        if self.started is not None and now - self.started > TIMEOUT:
            self.buffer.clear()
            self.started = None
            self.dropping = True
            self.errors += 1
        lines = []
        for b in data:
            if self.dropping:
                if b == 10:
                    self.dropping = False
                continue
            if b == 10:
                if self.buffer.strip():
                    lines.append(bytes(self.buffer))
                self.buffer.clear()
                self.started = None
                continue
            if self.started is None:
                self.started = now
            self.buffer.append(b)
            if len(self.buffer) >= MAX_LINE:
                self.errors += 1
                self.buffer.clear()
                self.started = None
                self.dropping = True
        return lines
