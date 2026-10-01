"""Original, bounded TCP transport for short HSFZ/DoIP diagnostic transactions."""
import math
import socket
import struct
import time

from .core import CliError


class Transport:
    MAX_PAYLOAD = 1024 * 1024

    def __init__(self, profile, timeout=5, observer=None):
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise CliError('Diagnostic timeout must be positive and at most 120 seconds')
        self.profile, self.timeout, self.observer = profile, timeout, observer
        self.sock = None
        self.source = profile['source']
        self.doip = profile['transport'] == 'doip'
        self.version = profile.get('protocol_version', 2)

    def __enter__(self):
        try:
            self.sock = socket.create_connection((self.profile['host'], self.profile['port']), self.timeout)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self.doip:
                deadline = time.monotonic() + self.timeout
                self.send_frame(5, struct.pack('!HB', self.source, self.profile.get('activation_type', 0)) + bytes(4), deadline)
                kind, body = self.receive_frame(deadline)
                if kind != 6 or len(body) not in (9, 13) or int.from_bytes(body[:2], 'big') != self.source or body[4] != 0x10:
                    raise CliError('DoIP routing activation was denied or malformed', 3)
            return self
        except BaseException as exc:
            self.close()
            if isinstance(exc, OSError):
                raise CliError(f'Diagnostic connection failed: {exc}', 3) from exc
            raise

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError('Diagnostic request deadline expired; no automatic retry', 3)
        self.sock.settimeout(remaining)

    def exact(self, count, deadline):
        result = bytearray()
        while len(result) < count:
            self.remaining(deadline)
            block = self.sock.recv(count - len(result))
            if not block:
                raise CliError('Diagnostic peer closed a partial or unanswered transaction', 3)
            result.extend(block)
        return bytes(result)

    def send_frame(self, kind, body, deadline):
        if len(body) > self.MAX_PAYLOAD:
            raise CliError('Diagnostic payload exceeds size limit', 3)
        header = (struct.pack('!BBHI', self.version, self.version ^ 255, kind, len(body))
                  if self.doip else struct.pack('!IH', len(body), kind))
        if self.observer:
            self.observer('tx', header + body)
        self.remaining(deadline)
        self.sock.sendall(header + body)

    def receive_frame(self, deadline):
        header = self.exact(8 if self.doip else 6, deadline)
        if self.doip:
            version, inverse, kind, count = struct.unpack('!BBHI', header)
            if version != self.version or inverse != version ^ 255:
                raise CliError('Invalid DoIP protocol version/header', 3)
        else:
            count, kind = struct.unpack('!IH', header)
        if count > self.MAX_PAYLOAD:
            raise CliError('Diagnostic peer advertised an oversized frame', 3)
        body = self.exact(count, deadline)
        if self.observer:
            self.observer('rx', header + body)
        return kind, body

    def request(self, target, payload):
        if not payload or len(payload) > 4096:
            raise CliError('Invalid diagnostic request')
        deadline = time.monotonic() + self.timeout
        addresses = struct.pack('!HH', self.source, target) if self.doip else bytes([self.source, target])
        reply_addresses = struct.pack('!HH', target, self.source) if self.doip else bytes([target, self.source])
        try:
            self.send_frame(0x8001 if self.doip else 1, addresses + payload, deadline)
            for _ in range(256):
                kind, body = self.receive_frame(deadline)
                if self.doip and kind == 7:
                    if body:
                        raise CliError('Malformed DoIP alive check', 3)
                    self.send_frame(8, struct.pack('!H', self.source), deadline)
                    continue
                if kind == (0x8002 if self.doip else 2):
                    if self.doip:
                        if len(body) < 5 or body[:4] != reply_addresses or body[4] != 0:
                            raise CliError('Invalid DoIP diagnostic acknowledgement', 3)
                    elif len(body) < 2 or body[:2] != addresses:
                        raise CliError('Invalid HSFZ acknowledgement', 3)
                    continue
                if kind != (0x8001 if self.doip else 1):
                    raise CliError(f'Diagnostic transport rejected or unsupported frame: 0x{kind:04X}', 3)
                size = len(reply_addresses)
                if len(body) <= size or body[:size] != reply_addresses:
                    raise CliError('Diagnostic response ECU/tester addresses do not match request', 3)
                response = body[size:]
                if response[0] == 0x7F:
                    if len(response) != 3 or response[1] != payload[0]:
                        raise CliError('Malformed or unrelated UDS negative response', 3)
                    if response[2] == 0x78:
                        continue
                    raise CliError(f'ECU rejected service 0x{payload[0]:02X}, NRC 0x{response[2]:02X}', 3,
                                   {'service': payload[0], 'negative_response_code': response[2]})
                if response[0] != payload[0] + 0x40:
                    raise CliError('UDS response service does not match request', 3)
                return response
            raise CliError('Too many diagnostic control/pending frames', 3)
        except OSError as exc:
            raise CliError(f'Diagnostic transport failed: {exc}; no automatic retry', 3) from exc
