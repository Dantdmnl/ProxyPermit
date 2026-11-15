"""Use real TLS sockets to prove credentials stay off unverified connections."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.web import ha_replication as replication
from app.core.tinyproxy import ConfigError


class CertificateMetadataPermissionsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned legacy TLS fixture.')
    def test_optional_metadata_does_not_require_access_to_private_legacy_tls_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.chmod(0o755)
            private = root / 'ha-tls'
            private.mkdir(mode=0o700)
            (private / 'key.pem').write_text('isolated private-key fixture')
            relative = replication.CERTIFICATE_TRANSITION.relative_to('/etc/proxypermit')
            code = ('import os; from pathlib import Path; from app.web import ha_replication as replication\n'
                    f'replication.CERTIFICATE_TRANSITION=Path({str(root / relative)!r})\n'
                    'os.setgroups([]); os.setgid(65534); os.setuid(65534)\n'
                    'assert replication.certificate_transition() is None\n'
                    f'key=Path({str(private / "key.pem")!r})\n'
                    'try: key.read_text()\n'
                    'except PermissionError: pass\n'
                    'else: raise AssertionError("The legacy private key became readable")\n')
            subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for generated TLS fixtures.')
class PeerTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        for name in ('valid', 'expired', 'other'):
            key, csr, cert = (cls.root / (name + suffix) for suffix in ('.key', '.csr', '.crt'))
            subprocess.run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=isolated-ha-test',
                            '-keyout', str(key), '-out', str(csr)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['openssl', 'x509', '-req', '-in', str(csr), '-signkey', str(key),
                            '-days', '0' if name == 'expired' else '1', '-out', str(cert)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.1)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def server(self, name):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # pylint: disable=invalid-name
                seen.append(self.headers.get('Authorization'))
                content = json.dumps({'test': True}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.root / (name + '.crt')), str(self.root / (name + '.key')))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port, seen

    def pin(self, name):
        der = ssl.PEM_cert_to_DER_cert((self.root / (name + '.crt')).read_text())
        return hashlib.sha256(der).hexdigest()

    def fetch(self, pin, ports):
        original = http.client.HTTPSConnection
        targets = iter(ports)
        def connection(_host, context):
            return original('127.0.0.1', next(targets), context=context, timeout=5)
        with patch('app.web.ha_replication._connection', side_effect=connection), \
                patch.dict('os.environ', {'NODE0_ADDRESS': '192.0.2.11'}):
            return replication.fetch_primary(pin, 'a' * 64, 'policy')

    def test_valid_pin_and_dates_allow_only_verified_request(self):
        port, seen = self.server('valid')
        self.assertEqual(self.fetch(self.pin('valid'), [port, port]), {'test': True})
        self.assertEqual(seen, ['Bearer ' + 'a' * 64])

    def test_wrong_pin_never_sends_bearer(self):
        port, seen = self.server('valid')
        with self.assertRaises(ConfigError):
            self.fetch(self.pin('other'), [port])
        self.assertEqual(seen, [])

    def test_explicit_candidate_pin_can_connect_without_trusting_system_cas(self):
        port, seen = self.server('other')
        reply = self.fetch([self.pin('valid'), self.pin('other')], [port, port])
        self.assertEqual(reply.verified_pin, self.pin('other'))
        self.assertEqual(seen, ['Bearer ' + 'a' * 64])

    def test_expired_candidate_is_rejected_even_during_dual_pin_window(self):
        port, seen = self.server('expired')
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.fetch([self.pin('valid'), self.pin('expired')], [port, port])
        self.assertEqual(seen, [])

    def test_expired_but_correctly_pinned_certificate_never_receives_bearer(self):
        port, seen = self.server('expired')
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.fetch(self.pin('expired'), [port, port])
        self.assertEqual(seen, [])

    def test_certificate_switch_between_handshakes_never_receives_bearer(self):
        first, seen_first = self.server('valid')
        second, seen_second = self.server('other')
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.fetch(self.pin('valid'), [first, second])
        self.assertEqual(seen_first + seen_second, [])
