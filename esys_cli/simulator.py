"""Loopback-only synthetic ECU simulator. Never connects to a vehicle."""
import socket
import struct
import threading


class Simulator:
    def __init__(self, protocol='hsfz', *, ecus=None, persistent=False, drop_after_clear=False, post_clear_records=b''):
        if protocol not in ('hsfz', 'doip'):
            raise ValueError('Unsupported simulation protocol')
        self.protocol = protocol
        self.ecus = ecus or ([0x29] if protocol == 'hsfz' else [0x1029])
        self.vin = 'WBA00000000000001'
        self.faults = {ecu: bytes.fromhex('12345609') for ecu in self.ecus}
        self.persistent, self.drop_after_clear = persistent, drop_after_clear
        self.post_clear_records = post_clear_records
        self.requests, self.errors = [], []
        self.stop = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.profile = {'schema_version': 1, 'kind': 'uds-profile', 'transport': protocol,
                        'host': '127.0.0.1', 'port': self.listener.getsockname()[1],
                        'source': 0xF4 if protocol == 'hsfz' else 0x0E00,
                        'identity_ecu': 0x10 if protocol == 'hsfz' else 0x1010,
                        'ecus': self.ecus, 'expected_vin': self.vin, 'protocol_version': 2, 'activation_type': 0}

    def __enter__(self):
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.worker.join(4)
        self.listener.close()
        if self.worker.is_alive():
            raise RuntimeError('Simulator failed to stop')
        if self.errors and args[0] is None:
            raise self.errors[0]

    def exact(self, connection, count):
        result = b''
        while len(result) < count:
            data = connection.recv(count - len(result))
            if not data:
                raise EOFError
            result += data
        return result

    def frame(self, kind, payload):
        if self.protocol == 'doip':
            return bytes.fromhex('02fd') + struct.pack('!HI', kind, len(payload)) + payload
        return struct.pack('!IH', len(payload), kind) + payload

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(2)
                try:
                    self.handle(connection)
                except (EOFError, ConnectionError, socket.timeout):
                    pass
                except Exception as exc:
                    self.errors.append(exc)

    def handle(self, connection):
        is_doip = self.protocol == 'doip'
        while not self.stop.is_set():
            header = self.exact(connection, 8 if is_doip else 6)
            if is_doip:
                version, inverse, kind, count = struct.unpack('!BBHI', header)
                if (version, inverse) != (2, 253):
                    raise ValueError('Unexpected simulator protocol header')
            else:
                count, kind = struct.unpack('!IH', header)
            if count > 4096:
                raise ValueError('Oversized simulator request')
            body = self.exact(connection, count)
            if is_doip and kind == 5:
                connection.sendall(self.frame(6, body[:2] + bytes.fromhex('10001000000000')))
                continue
            if kind != (0x8001 if is_doip else 1):
                raise ValueError('Unexpected simulator message type')
            address_size = 4 if is_doip else 2
            source, target = struct.unpack('!HH' if is_doip else '!BB', body[:address_size])
            request = body[address_size:]
            self.requests.append((target, request))
            if request == bytes.fromhex('22f190'):
                response = bytes.fromhex('62f190') + self.vin.encode('ascii')
            elif request == bytes.fromhex('22f189'):
                response = bytes.fromhex('62f189') + b'SIM-1.0'
            elif request == bytes.fromhex('1902ff') and target in self.faults:
                response = bytes.fromhex('5902ff') + self.faults[target]
            elif request == bytes.fromhex('14ffffff') and target in self.faults:
                if not self.persistent:
                    self.faults[target] = self.post_clear_records
                if self.drop_after_clear:
                    return
                response = b'\x54'
            else:
                response = bytes([0x7F, request[0], 0x11])
            addresses = struct.pack('!HH' if is_doip else '!BB', target, source)
            acknowledgement = self.frame(0x8002, addresses + b'\x00') if is_doip else self.frame(2, body)
            connection.sendall(acknowledgement + self.frame(0x8001 if is_doip else 1, addresses + response))
