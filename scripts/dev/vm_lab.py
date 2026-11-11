#!/usr/bin/env python3
"""Run installer and proxy checks on disposable Debian or Ubuntu VMs."""
import argparse
import http.client
import http.cookiejar
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
INSTALL_DIR = Path('/opt/proxypermit-vm-lab')
STATE = Path('/var/lib/proxypermit-vm-lab')
MARKER = Path('/etc/proxypermit-disposable-vm')
PROXY_PORT = 3128
MANAGER_PORT = 8080
USERNAME = 'lab-admin'


def run(*command):
    return subprocess.run(command, cwd=ROOT, check=True)


def require_lab():
    if sys.platform != 'linux' or os.geteuid() != 0:  # pylint: disable=no-member
        raise ValueError('Run this command as root inside a disposable Linux VM.')
    if not MARKER.is_file() or MARKER.is_symlink():
        raise ValueError(f'Missing lab marker {MARKER}. See the VM lab guide.')
    info = MARKER.stat()
    if info.st_uid != 0 or info.st_mode & 0o022 or MARKER.read_text().strip() != socket.gethostname():
        raise ValueError('The lab marker must be root-owned, protected, and contain this VM hostname.')
    if not Path('/run/systemd/system').is_dir():
        raise ValueError('This lab requires a running systemd instance.')


def install(*options):
    run('bash', str(ROOT / 'install.sh'), '--install-dir', str(INSTALL_DIR), *options)


def assert_lab_installation():
    marker = INSTALL_DIR / 'installed.json'
    if not marker.is_file() or not (STATE / 'password').is_file():
        raise ValueError('Install the VM lab before running this command.')
    fragment = Path('/etc/systemd/system/proxypermit.service')
    if f'WorkingDirectory={INSTALL_DIR}/current' not in fragment.read_text().splitlines():
        raise ValueError('The active ProxyPermit unit does not belong to this lab.')
    return json.loads(marker.read_text())


def manager_client():
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, *, data=None, expected=200):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(f'http://127.0.0.1:{MANAGER_PORT}{path}', data=body,
                      headers={'Content-Type': 'application/json'})
        try:
            response = opener.open(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise RuntimeError(f'{path}: expected HTTP {expected}, got {response.status}.')
            return json.load(response)

    request('/api/proxy/config', expected=401)
    request('/api/auth/login', data={'username': USERNAME, 'password': (STATE / 'password').read_text().strip()})
    if request('/api/auth/me')['username'] != USERNAME:
        raise RuntimeError('The console did not establish the expected administrator session.')
    request('/api/proxy/config')
    return request


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):  # pylint: disable=invalid-name
        if self.path != '/proxypermit-lab':
            self.send_error(404)
            return
        body = f'ProxyPermit lab: {socket.gethostname()}\n'.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def proxy_request(proxy, origin_host='127.0.0.1', origin_port=18080):
    connection = http.client.HTTPConnection(proxy, PROXY_PORT, timeout=3)
    try:
        connection.request('GET', f'http://{origin_host}:{origin_port}/proxypermit-lab')
        response = connection.getresponse()
        return response.status, response.read(1024)
    finally:
        connection.close()


def check_node():
    installed = assert_lab_installation()
    manager_client()
    if installed['mode'] == 'ha':
        run('systemctl', 'is-active', '--quiet', 'keepalived.service')
        rules = subprocess.run(['ufw', 'show', 'added'], capture_output=True, text=True, check=True).stdout
        for label in ('HA IN - Tinyproxy clients', 'HA IN - Keepalived VRRP', 'HA OUT - Keepalived VRRP'):
            if label not in rules:
                raise RuntimeError(f'Missing UFW rule: {label}')
    with ThreadingHTTPServer(('127.0.0.1', 0), Origin) as origin:
        thread = Thread(target=origin.serve_forever, daemon=True)
        thread.start()
        try:
            port = origin.server_address[1]
            status, body = proxy_request('127.0.0.1', origin_port=port)
            if status != 200 or body != f'ProxyPermit lab: {socket.gethostname()}\n'.encode():
                raise RuntimeError(f'The allowed test origin did not return the expected response (HTTP {status}).')
            status, _ = proxy_request('127.0.0.1', origin_host='127.0.0.2', origin_port=port)
            if status != 403:
                raise RuntimeError(f'The blocked test destination returned HTTP {status}, expected 403.')
        finally:
            origin.shutdown()
            thread.join()
    print('Console authentication, allowed proxy traffic, and blocked destination passed.')


def fresh_install(args):
    if any(path.exists() or path.is_symlink() for path in (
        INSTALL_DIR, STATE, Path('/etc/proxypermit'), Path('/etc/systemd/system/proxypermit.service'),
    )):
        raise ValueError('Restore a clean VM snapshot before starting a fresh lab installation.')
    options = ['--mode', args.mode, '--server', args.server]
    fields = ('ha_node', 'ha_interface', 'ha_node_a', 'ha_node_b', 'ha_vip', 'ha_client_cidr', 'ha_vrid')
    if args.mode == 'ha':
        if any(getattr(args, field) is None for field in fields):
            raise ValueError('Supply all HA options listed in install --help.')
        for field in fields:
            options.extend(('--' + field.replace('_', '-'), getattr(args, field)))
    elif any(getattr(args, field) is not None for field in fields):
        raise ValueError('HA options require --mode ha.')
    install('--plan', *options)
    STATE.mkdir(mode=0o700)
    password = STATE / 'password'
    with os.fdopen(os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
        handle.write(secrets.token_urlsafe(24) + '\n')
    install('--apply', '--admin-user', USERNAME, '--admin-password-file', str(password), *options)
    print(f'GUI username: {USERNAME}. Password file (root only): {password}', flush=True)
    manager_client()('/api/entries', data={'content': '127.0.0.1', 'auto_restart': False})
    run('systemctl', 'restart', 'tinyproxy.service')
    check_node()


def lifecycle():
    before = assert_lab_installation()
    if before['mode'] != 'standalone':
        raise ValueError('Run the lifecycle check on a standalone lab VM.')
    install('--upgrade', '--plan')
    install('--upgrade', '--apply')
    after = assert_lab_installation()
    if after['release'] == before['release']:
        raise RuntimeError('The upgrade did not switch releases.')
    check_node()
    install('--rollback', Path(after['backup']).name, '--apply')
    if assert_lab_installation()['release'] != before['release']:
        raise RuntimeError('Rollback did not restore the original release.')
    check_node()

    backups = INSTALL_DIR / 'backups'
    prior = set(backups.iterdir())
    install('--uninstall', '--plan')
    install('--uninstall', '--apply')
    if (INSTALL_DIR / 'current').is_symlink():
        raise RuntimeError('The active release link remained after uninstall.')
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'proxypermit.service'], check=False).returncode == 0:
        raise RuntimeError('The management service remained active after uninstall.')
    run('systemctl', 'is-active', '--quiet', 'tinyproxy.service')
    if Path('/etc/systemd/system/proxypermit-audit-retention.service').is_file():
        run('systemctl', 'is-active', '--quiet', 'proxypermit-audit-retention.timer', 'proxypermit-journal-retention.timer')
        run('systemctl', 'start', 'proxypermit-audit-retention.service', 'proxypermit-journal-retention.service')
    created = set(backups.iterdir()) - prior
    if len(created) != 1:
        raise RuntimeError('Could not identify the uninstall recovery backup.')
    install('--rollback', created.pop().name, '--apply')
    check_node()


def check_vip(vip, expected_node):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            status, body = proxy_request(str(vip))
            if status == 200 and body == f'ProxyPermit lab: {expected_node}\n'.encode():
                print(f'VIP {vip} served the test request through {expected_node}.')
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.5)
    raise RuntimeError(f'VIP {vip} did not return the expected response from {expected_node}.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    setup = actions.add_parser('install', help='Install and test a fresh disposable VM.')
    setup.add_argument('--mode', choices=('standalone', 'ha'), default='standalone')
    setup.add_argument('--server', choices=('gunicorn', 'granian'), default='gunicorn')
    for option in ('node', 'interface', 'node-a', 'node-b', 'vip', 'client-cidr', 'vrid'):
        setup.add_argument('--ha-' + option)
    actions.add_parser('check', help='Check console access and proxy traffic on an existing lab VM.')
    actions.add_parser('lifecycle', help='Exercise upgrade, rollback, uninstall, and restore on a standalone lab VM.')
    actions.add_parser('origin', help='Serve a loopback test origin on an HA node.')
    vip = actions.add_parser('check-vip', help='Check the HA proxy from a lab client.')
    vip.add_argument('address', type=ipaddress.IPv4Address)
    vip.add_argument('expected_node')
    args = parser.parse_args()
    try:
        if args.action == 'check-vip':
            check_vip(args.address, args.expected_node)
        elif args.action == 'origin':
            with ThreadingHTTPServer(('127.0.0.1', 18080), Origin) as origin:
                print(f'Loopback test origin on {socket.gethostname()}:18080', flush=True)
                origin.serve_forever()
        else:
            require_lab()
            if args.action == 'install':
                fresh_install(args)
            elif args.action == 'check':
                check_node()
            else:
                lifecycle()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'VM lab stopped: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
