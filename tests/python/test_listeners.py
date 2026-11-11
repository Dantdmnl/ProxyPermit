"""Traffic-free Linux listener checks, including generated HA helper behavior."""
import io
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.listeners import tcp_listener
from scripts.deploy.install import KEEPALIVED_HEALTH_TEXT


class ListenerTests(unittest.TestCase):
    def tables(self, ipv4='', ipv6=''):
        def open_table(path, **_options):
            return io.StringIO('header\n' + (ipv4 if path.name == 'tcp' else ipv6))
        return patch.object(Path, 'open', open_table)

    def test_only_listen_state_and_exact_port_match(self):
        with self.tables('0: 0100007F:0C38 00000000:0000 01\n'
                         '1: 00000000:0C39 00000000:0000 0A\n'):
            self.assertFalse(tcp_listener(3128))
        with self.tables('malformed\n0: malformed 00000000:0000 0A\n'
                         '1: 00000000:0C38 00000000:0000 0A\n'):
            self.assertTrue(tcp_listener(3128))

    def test_ipv6_listener_and_missing_protocol_tables(self):
        with self.tables(ipv6='0: 00000000000000000000000000000000:0C38 0000:0000 0A\n'):
            self.assertTrue(tcp_listener(3128))
        with patch.object(Path, 'open', side_effect=FileNotFoundError):
            self.assertFalse(tcp_listener(3128))
        with patch.object(Path, 'open', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                tcp_listener(3128)

    def test_invalid_ports_are_rejected_without_reading_kernel_state(self):
        with patch.object(Path, 'open') as read:
            for port in (0, -1, 65536, True, '3128', None):
                with self.assertRaises(ValueError):
                    tcp_listener(port)
            read.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux', 'Requires Linux TCP tables.')
    def test_real_listener_and_generated_helper_never_connect(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            self.assertFalse(tcp_listener(port))
            listener.listen()
            listener.setblocking(False)
            with patch.object(socket, 'create_connection') as connection:
                self.assertTrue(tcp_listener(port))
                connection.assert_not_called()
            helper = KEEPALIVED_HEALTH_TEXT.replace('tcp_listener(3128)', f'tcp_listener({port})')
            self.assertEqual(subprocess.run([sys.executable, '-c', helper], check=False).returncode, 0)
            with self.assertRaises(BlockingIOError):
                listener.accept()
        self.assertFalse(tcp_listener(port))
        self.assertEqual(subprocess.run([sys.executable, '-c', helper], check=False).returncode, 1)
