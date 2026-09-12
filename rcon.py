"""Small Minecraft RCON transport. Never include credentials in diagnostics."""
import re
import json
import socket
import struct
from pathlib import Path


def properties(path):
    def unescape(value):
        return re.sub(r'\\u([0-9a-fA-F]{4})|\\(.)',
                      lambda m: chr(int(m[1], 16)) if m[1] else
                      {'t': '\t', 'n': '\n', 'r': '\r', 'f': '\f'}.get(m[2], m[2]), value)
    result = {}
    logical = ''
    for line in Path(path).read_text(encoding='latin-1').splitlines():
        logical += line.lstrip() if logical else line
        if len(logical) - len(logical.rstrip('\\')) & 1:
            logical = logical[:-1]
            continue
        text = logical.lstrip()
        logical = ''
        if not text or text[0] in '#!':
            continue
        match = re.match(r'((?:\\.|[^\s=:])+)(?:\s*[=:]\s*|\s+)?(.*)', text)
        if match:
            result[unescape(match[1])] = unescape(match[2])
    return result


class Rcon:
    def __init__(self, root=None, timeout=15):
        if root is None:
            root = json.loads((Path(__file__).parent/'config.json').read_text())['server_root']
        self.props = properties(Path(root) / 'server.properties')
        if self.props.get('enable-rcon') != 'true':
            raise RuntimeError('RCON is disabled in server.properties')
        self.sock = socket.create_connection(('127.0.0.1', int(self.props['rcon.port'])), timeout)
        try:
            self.send(1, 3, self.props['rcon.password'])
            while True:
                rid, kind, _ = self.read()
                if rid == -1:
                    raise RuntimeError('RCON authentication rejected')
                if rid == 1 and kind == 2:
                    break
        except BaseException:
            self.close()
            raise

    def send(self, rid, kind, text):
        data = struct.pack('<ii', rid, kind) + text.encode('utf-8') + b'\0\0'
        self.sock.sendall(struct.pack('<i', len(data)) + data)

    def exact(self, count):
        data = b''
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise RuntimeError('RCON connection closed')
            data += chunk
        return data

    def read(self):
        length, = struct.unpack('<i', self.exact(4))
        if not 10 <= length <= 1048576:
            raise RuntimeError('Invalid RCON packet length')
        packet = self.exact(length)
        rid, kind = struct.unpack('<ii', packet[:8])
        if packet[-2:] != b'\0\0':
            raise RuntimeError('Invalid RCON packet terminator')
        return rid, kind, packet[8:-2]

    def command(self, command):
        self.send(2, 2, command)
        # Minecraft replies to this unsupported packet type with the same ID.
        # It acts as a boundary after all response fragments for command 2.
        self.send(3, 0, '')
        parts = []
        while True:
            rid, _, text = self.read()
            if rid == 3:
                return b''.join(parts).decode('utf-8', errors='replace')
            if rid != 2:
                raise RuntimeError('Unexpected RCON response ID')
            parts.append(text)
            if sum(map(len, parts)) > 8 * 1024 * 1024:
                raise RuntimeError('RCON response exceeds limit')

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
