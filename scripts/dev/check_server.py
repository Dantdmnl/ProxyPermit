#!/usr/bin/env python3
"""Exercise a real WSGI server with disposable files and an ephemeral port."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import http.cookiejar
import json
import os
import secrets
import signal
import ssl
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.runtime.server import build_command


def main():
    import pyotp
    from werkzeug.security import generate_password_hash
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['gunicorn', 'granian'], default='gunicorn')
    parser.add_argument('--deployment', choices=['standalone', 'ha'], default='standalone')
    parser.add_argument('--tls', action='store_true', help='Verify direct HTTPS, secure cookies and security headers.')
    args = parser.parse_args()
    windows = os.name == 'nt'
    workers = '1' if windows else '2'
    with tempfile.TemporaryDirectory(prefix='proxypermit-http-') as directory:
        state = Path(directory)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        password = secrets.token_urlsafe(24)
        otp = pyotp.TOTP(pyotp.random_base32())
        account_file = state / 'accounts.json'
        hashed = generate_password_hash(password)
        account_file.write_text(json.dumps({
            'smoke-admin': {'password_hash': hashed, 'revision': 'admin-v1'},
            'smoke-mfa': {'password_hash': hashed, 'revision': 'mfa-v1', 'totp_secret': otp.secret},
        }), encoding='utf-8')
        config = 'Port 3128\nAllow 127.0.0.1\nConnectPort 443\n'
        (state / 'tinyproxy.conf').write_text(config)
        (state / 'filter.txt').write_text('# isolated smoke test\n')
        env = dict(os.environ, FLASK_SECRET_KEY=secrets.token_hex(32), ENABLE_AUTH='true', AUTH_ACCOUNTS_FILE=str(account_file),
                   ENABLE_RADIUS='false', SESSION_COOKIE_SECURE='false', MANAGER_SCHEME='http',
                   GUNICORN_USE_TLS='false', APP_SERVER=args.backend, APP_WORKERS=workers,
                   APP_BIND=f'127.0.0.1:{port}', DEPLOYMENT_MODE=args.deployment, DEMO_MODE='true',
                   PROXYPERMIT_MANAGED_HA='true' if args.deployment == 'ha' else 'false',
                   WHITELIST_PATH=str(state / 'filter.txt'), TINYPROXY_CONFIG_PATH=str(state / 'tinyproxy.conf'),
                   SESSION_STATE_FILE=str(state / 'session.json'), AUDIT_LOG_PATH=str(state / 'audit.log'),
                   PROXYPERMIT_ACCESS_SAFETY_FILE=str(state / 'access-safety.json'),
                   SYNC_SECRET_FILE=str(state / 'sync.key'), WHITELIST_ENTRY_LOG_PATH=str(state / 'entries.json'))
        scheme = 'https' if args.tls else 'http'
        handlers = [ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if args.tls:
            cert, key = state / 'cert.pem', state / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                            '-nodes', '-days', '1', '-subj', '/CN=isolated TLS test',
                            '-addext', 'subjectAltName=IP:127.0.0.1', '-keyout', str(key), '-out', str(cert)],
                           check=True, capture_output=True)
            env.update(MANAGER_SCHEME='https', GUNICORN_USE_TLS='true', MANAGER_CERT=str(cert), MANAGER_KEY=str(key),
                       SESSION_COOKIE_SECURE='true', ENABLE_HSTS='true', HSTS_MAX_AGE='86400')
            handlers.append(HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
        opener = build_opener(*handlers)

        def request(path, method='GET', body=None, headers=None, expected=200):
            data = None if body is None else json.dumps(body).encode()
            req = Request(f'{scheme}://127.0.0.1:{port}{path}', data=data, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
            try:
                response = opener.open(req, timeout=3)
            except HTTPError as exc:
                response = exc
            with response:
                content = response.read()
                if response.status != expected:
                    raise RuntimeError(f'{path}: expected {expected}, received {response.status}.')
                if args.tls:
                    assert response.headers.get('Strict-Transport-Security') == 'max-age=86400'
                    cookies = response.headers.get_all('Set-Cookie', [])
                    assert all('Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie for cookie in cookies)
                    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
                    assert 'gunicorn' not in response.headers.get('Server', '').lower()
                return content

        with (state / 'server.log').open('w+', encoding='utf-8') as output:
            # Windows execv starts another process instead of replacing the
            # launcher. Keep the Granian master as the process we supervise.
            command = build_command(args.backend, env) if windows else [sys.executable, '-m', 'app.runtime.server']
            process = subprocess.Popen(command, cwd=ROOT, env=env,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0,
                                       stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        request('/')
                        break
                    except (URLError, TimeoutError, ConnectionError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            output.seek(0)
                            raise RuntimeError('Server startte niet:\n' + output.read()[-4000:]) from None
                        time.sleep(0.2)
                if args.tls:
                    # Browsers can preconnect without sending a TLS ClientHello.
                    # These idle sockets must not exhaust the HTTP workers.
                    idle = []
                    try:
                        for _ in range(6):
                            idle.append(socket.create_connection(('127.0.0.1', port), timeout=3))
                        def asset(_):
                            connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=10,
                                context=ssl.create_default_context(cafile=str(cert)))
                            try:
                                connection.request('GET', '/static/js/rules.js')
                                response = connection.getresponse()
                                assert response.status == 200 and b'RuleView' in response.read()
                            finally:
                                connection.close()
                        with ThreadPoolExecutor(max_workers=6) as pool:
                            list(pool.map(asset, range(12)))
                    finally:
                        for connection in idle:
                            connection.close()
                request('/api/proxy/config', expected=401)
                credentials = {'username': 'smoke-mfa', 'password': password}
                challenge = json.loads(request('/api/auth/login', 'POST', credentials, expected=401))
                if not challenge.get('otpRequired'):
                    raise RuntimeError('Het 2FA-account moet om een authenticatorcode vragen.')
                request('/api/proxy/config', expected=401)
                credentials['otp'] = otp.now()
                request('/api/auth/login', 'POST', credentials)
                if json.loads(request('/api/auth/me'))['username'] != 'smoke-mfa':
                    raise RuntimeError('Verkeerde sessie-identiteit na 2FA.')
                request('/api/auth/logout', 'POST')
                request('/api/auth/login', 'POST', credentials, expected=401)
                request('/api/auth/login', 'POST', {'username': 'smoke-admin', 'password': password})
                loaded = json.loads(request('/api/proxy/config'))
                request('/proxy')
                for asset in ('css/proxy.css', 'css/console.css', 'js/proxy.js', 'css/manager.css',
                              'js/manager.js', 'js/manager-settings.js', 'js/rules.js', 'js/rule-dialog.js',
                              'js/shell.js', 'js/theme.js', 'css/theme.css', 'js/i18n.js', 'js/version.js',
                              'js/signin.js', 'css/signin.css', 'css/workspace-dialogs.css'):
                    request('/static/' + asset)
                request('/api/proxy/config', 'PUT', {'content': config + 'Timeout 120\n', 'revision': loaded['revision']},
                        {'X-CSRF-Token': loaded['csrfToken']})
                saved = json.loads(request('/api/proxy/config'))
                if not saved['draft'] or (state / 'tinyproxy.conf').read_text() != config:
                    raise RuntimeError('Een opgeslagen concept mag het actieve proxybestand niet wijzigen.')
                events = [json.loads(line) for line in (state / 'audit.log').read_text(encoding='utf-8').splitlines()]
                if not any(event.get('event') == 'tinyproxy_config_saved' and event.get('username') == 'smoke-admin'
                           for event in events):
                    raise RuntimeError('Auditlog mist het account dat de wijziging heeft opgeslagen.')
                request('/api/proxy/config', 'PUT', {'content': config, 'revision': loaded['revision']},
                        {'X-CSRF-Token': loaded['csrfToken']}, expected=409)
                print(f'{args.backend}/{args.deployment}: {scheme.upper()}, accounts, 2FA, audit and save checks passed (workers: {workers}).')
                return 0
            finally:
                try:
                    if process.poll() is None:
                        if windows:
                            process.send_signal(signal.CTRL_BREAK_EVENT)  # pylint: disable=no-member
                        else:
                            process.terminate()
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    if windows:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       check=False, capture_output=True)
                    else:
                        process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
