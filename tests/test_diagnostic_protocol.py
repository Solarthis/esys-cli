from contextlib import contextmanager
import socket
import struct
import sys
import threading
import time
import unittest

from esys_cli.core import CliError


def recv_exact(sock, count):
    data = b''
    while len(data) < count:
        block = sock.recv(count - len(data))
        if not block:
            raise EOFError('test peer disconnected')
        data += block
    return data


@contextmanager
def peer(handler):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(3)
    errors = []
    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                handler(connection)
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()
        worker.join(4)
        if worker.is_alive():
            raise AssertionError('Test peer failed to terminate')
        if errors and sys.exc_info()[0] is None:
            raise errors[0]


def hsfz(body, control=1):
    return struct.pack('!IH', len(body), control) + body


def doip(kind, body):
    return bytes.fromhex('02fd') + struct.pack('!HI', kind, len(body)) + body


class ProtocolTests(unittest.TestCase):
    def transport(self, port, protocol='hsfz', timeout=1):
        from esys_cli.diagnostic_transport import Transport
        return Transport({'transport': protocol, 'host': '127.0.0.1', 'port': port,
                          'source': 0xF4 if protocol == 'hsfz' else 0x0E00,
                          'protocol_version': 2, 'activation_type': 0}, timeout)

    def test_literal_hsfz_request_fragmentation_ack_and_pending(self):
        from esys_cli.uds import read_faults
        def handler(sock):
            self.assertEqual(recv_exact(sock, 11), bytes.fromhex('000000050001f4291902ff'))
            wire = hsfz(bytes.fromhex('f4291902ff'), 2) + hsfz(bytes.fromhex('29f47f1978')) + hsfz(bytes.fromhex('29f45902ff12345609'))
            for octet in wire:
                sock.sendall(bytes([octet]))
        with peer(handler) as port, self.transport(port) as client:
            result = read_faults(client, 0x29)
        self.assertEqual(result['status_availability_mask'], 255)
        self.assertEqual(result['dtcs'][0]['code'], '123456')
        self.assertEqual(result['dtcs'][0]['status'], 9)
        self.assertEqual(result['dtcs'][0]['status_flags'], ['test_failed', 'confirmed'])

    def test_doip_activation_alive_ack_and_vin(self):
        from esys_cli.uds import read_vin
        def handler(sock):
            self.assertEqual(recv_exact(sock, 15), bytes.fromhex('02fd0005000000070e000000000000'))
            sock.sendall(doip(6, bytes.fromhex('0e0010001000000000')))
            self.assertEqual(recv_exact(sock, 15), bytes.fromhex('02fd8001000000070e00101022f190'))
            sock.sendall(doip(7, b''))
            self.assertEqual(recv_exact(sock, 10), bytes.fromhex('02fd0008000000020e00'))
            sock.sendall(doip(0x8002, bytes.fromhex('10100e0000')) + doip(0x8001, bytes.fromhex('10100e0062f190') + b'WBA00000000000001'))
        with peer(handler) as port, self.transport(port, 'doip') as client:
            self.assertEqual(read_vin(client, 0x1010), 'WBA00000000000001')

    def test_ack_only_is_never_diagnostic_success(self):
        def handler(sock):
            recv_exact(sock, 12)
            sock.sendall(hsfz(bytes.fromhex('f42914ffffff'), 2))
            time.sleep(0.2)
        with peer(handler) as port, self.transport(port, timeout=0.05) as client:
            with self.assertRaises(CliError):
                client.request(0x29, bytes.fromhex('14ffffff'))

    def test_wrong_address_service_negative_truncated_and_oversized_fail(self):
        responses = [hsfz(bytes.fromhex('30f45902ff')), hsfz(bytes.fromhex('29f454')),
                     hsfz(bytes.fromhex('29f47f1922')), hsfz(bytes.fromhex('29f47f2278')),
                     bytes.fromhex('002000000001'), bytes.fromhex('00000005000129')]
        for response in responses:
            with self.subTest(response=response.hex()):
                def handler(sock):
                    recv_exact(sock, 11)
                    sock.sendall(response)
                with peer(handler) as port, self.transport(port) as client:
                    with self.assertRaises(CliError):
                        client.request(0x29, bytes.fromhex('1902ff'))

    def test_doip_denied_activation_sends_no_diagnostic_request(self):
        def handler(sock):
            recv_exact(sock, 15)
            sock.sendall(doip(6, bytes.fromhex('0e0010000000000000')))
            self.assertEqual(sock.recv(1), b'')
        with peer(handler) as port:
            with self.assertRaises(CliError):
                with self.transport(port, 'doip'):
                    self.fail('Denied activation accepted')

    def test_malformed_dtc_and_wrong_did_echo_are_rejected(self):
        from esys_cli.uds import read_faults, read_did
        for reply, operation in [(bytes.fromhex('5902ff123456'), lambda client: read_faults(client, 0x29)),
                                 (bytes.fromhex('62f191abcd'), lambda client: read_did(client, 0x29, 0xF190))]:
            def handler(sock):
                recv_exact(sock, 11)
                sock.sendall(hsfz(bytes.fromhex('29f4') + reply))
            with peer(handler) as port, self.transport(port) as client:
                with self.assertRaises(CliError):
                    operation(client)


if __name__ == '__main__':
    unittest.main()
