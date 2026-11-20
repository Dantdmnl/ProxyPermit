#!/usr/bin/env python3
"""Check real Tinyproxy SIGUSR1 reloads using isolated files, ports and connections."""
import argparse
from contextlib import closing
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time


def available_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_until(check, description, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    raise RuntimeError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default='/usr/bin/tinyproxy')
    args = parser.parse_args()
    if os.name == 'nt':
        parser.error('Run inside a disposable Linux/WSL environment with Tinyproxy installed.')
    stop = threading.Event()

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):  # pylint: disable=invalid-name
            self.send_response(200)
            streaming = self.path == '/stream'
            self.send_header('Content-Length', '65536' if streaming else '2')
            self.end_headers()
            try:
                if not streaming:
                    self.wfile.write(b'ok')
                    return
                for _ in range(64):
                    self.wfile.write(b'x' * 1024)
                    self.wfile.flush()
                    if stop.wait(.1):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(15)
            while not stop.is_set():
                data = self.request.recv(4096)
                if not data:
                    break
                self.request.sendall(data)

    with tempfile.TemporaryDirectory(prefix='proxypermit-reload-') as directory, \
            ThreadingHTTPServer(('127.0.0.1', 0), Origin) as origin, \
            socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo) as echo:
        origin.daemon_threads = echo.daemon_threads = True
        for server in (origin, echo):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        root = Path(directory)
        filters, config, log = (root / name for name in ('filters', 'tinyproxy.conf', 'tinyproxy.log'))
        port = available_port()
        filters.write_text('^localhost(:[0-9]+)?$\n', encoding='utf-8')
        config.write_text(f'Port {port}\nListen 127.0.0.1\nAllow 127.0.0.1\nTimeout 60\nMaxClients 20\n'
                          f'LogFile "{log}"\nLogLevel Info\nPidFile "{root / "pid"}"\n'
                          f'Filter "{filters}"\nFilterType ere\nFilterDefaultDeny Yes\nConnectPort {echo.server_address[1]}\n', encoding='utf-8')
        with (root / 'stderr').open('w') as errors:
            process = subprocess.Popen([args.binary, '-d', '-c', str(config)], stdout=errors, stderr=errors)
            stream = tunnel = None
            try:
                def ready():
                    if process.poll() is not None:
                        raise RuntimeError('Isolated Tinyproxy did not start.')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            return True
                    except OSError:
                        return False
                wait_until(ready, 'Tinyproxy did not open its isolated port.')
                stream = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                stream.request('GET', f'http://localhost:{origin.server_port}/stream')
                response = stream.getresponse()
                if response.status != 200 or response.read(1024) != b'x' * 1024:
                    raise RuntimeError('Initial HTTP stream failed.')
                tunnel = socket.create_connection(('127.0.0.1', port), timeout=5)
                tunnel.sendall(f'CONNECT localhost:{echo.server_address[1]} HTTP/1.0\r\n\r\n'.encode())
                headers = b''
                while b'\r\n\r\n' not in headers:
                    chunk = tunnel.recv(1)
                    if not chunk:
                        raise RuntimeError('CONNECT closed before sending response headers.')
                    headers += chunk
                    if len(headers) > 8192:
                        raise RuntimeError('CONNECT headers too large.')
                if b' 200 ' not in headers.split(b'\r\n')[0]:
                    raise RuntimeError('Initial CONNECT request failed.')
                for iteration in range(3):
                    filters.write_text('^other\\.invalid$\n', encoding='utf-8')
                    desired = 200 if iteration == 1 else 403
                    config.write_text(config.read_text().replace('FilterDefaultDeny Yes', 'FilterDefaultDeny No') if iteration == 1
                                      else config.read_text().replace('FilterDefaultDeny No', 'FilterDefaultDeny Yes'), encoding='utf-8')
                    previous = log.read_text().count('Reloading config file finished')
                    process.send_signal(getattr(signal, 'SIGUSR1'))
                    wait_until(lambda: log.read_text().count('Reloading config file finished') > previous,
                               'Reload was not acknowledged.')
                    with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=3)) as fresh:
                        fresh.request('GET', f'http://localhost:{origin.server_port}/new')
                        denied = fresh.getresponse()
                        if denied.status != desired:
                            raise RuntimeError('New connections did not follow the reloaded filter.')
                        denied.read()
                    payload = f'connected-{iteration}'.encode()
                    tunnel.sendall(payload)
                    echoed = b''
                    while len(echoed) < len(payload):
                        chunk = tunnel.recv(len(payload) - len(echoed))
                        if not chunk:
                            raise RuntimeError('CONNECT tunnel was dropped during reload.')
                        echoed += chunk
                    if echoed != payload or response.read(1024) != b'x' * 1024 or process.poll() is not None:
                        raise RuntimeError('An existing connection failed during reload.')
                if response.read() != b'x' * (65536 - 4096):
                    raise RuntimeError('HTTP transfer did not finish after the reloads.')
                print('PASS: three acknowledged SIGUSR1 reloads; HTTP stream and CONNECT tunnel survived; new requests followed changed filters and allowlist/blocklist mode.')
            finally:
                stop.set()
                if tunnel:
                    tunnel.close()
                if stream:
                    stream.close()
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                for server in (origin, echo):
                    server.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
