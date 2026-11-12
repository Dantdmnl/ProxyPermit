"""Actual local HTTP responses and a listening but non-responsive server."""
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.core.proxy_health import HEALTH_PATH, proxy_responsive
from scripts.deploy.install import health_helper_text


class ProxyHealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.release = threading.Event()
        self.status = 200
        self.hang = False
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.requests.append((self.path, self.headers.get('Authorization'), self.headers.get('Proxy-Authorization')))
                if fixture.hang:
                    fixture.release.wait(2)
                    return
                self.send_response(fixture.status)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'OK')

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.config = Path(self.directory.name) / 'tinyproxy.conf'
        self.config.write_text(f'Port {self.server.server_port}\nStatHost local-proxy.stats\nBasicAuth example not-a-real-password\n', encoding='utf-8')

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_complete_local_http_and_auth_denials_without_credentials(self):
        for status in (200, 401, 403, 407):
            self.status = status
            self.assertTrue(proxy_responsive(self.config))
        self.assertTrue(all(path == 'http://local-proxy.stats' + HEALTH_PATH and not auth and not proxy_auth
                            for path, auth, proxy_auth in self.requests))
        self.status = 503
        self.assertFalse(proxy_responsive(self.config))

    def test_hung_server_is_unhealthy_even_with_an_open_port(self):
        self.hang = True
        with socket.create_connection(self.server.server_address, timeout=.2):
            pass
        self.assertFalse(proxy_responsive(self.config, timeout=.05))

    def test_generated_helper_and_invalid_settings_fail_closed(self):
        helper = health_helper_text(self.config)
        self.assertEqual(subprocess.run([sys.executable, '-c', helper], check=False).returncode, 0)
        self.status = 503
        self.assertEqual(subprocess.run([sys.executable, '-c', helper], check=False).returncode, 1)
        self.config.write_text('Port 0\nStatHost "bad host"\n', encoding='utf-8')
        self.assertFalse(proxy_responsive(self.config))
