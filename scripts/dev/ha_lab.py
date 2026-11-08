"""Run a two-node Tinyproxy/Keepalived lab on a disposable Linux test host."""
import argparse
import http.client
import json
import os
import secrets
import select
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pyotp
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.deploy import install

NETWORK = '198.18.77'
VIP = NETWORK + '.100'


def run(*command, **options):
    return subprocess.run(command, check=True, **options)


def wait_for(check):
    for _ in range(80):
        try:
            if check():
                return
        except (OSError, http.client.HTTPException, subprocess.SubprocessError):
            pass
        time.sleep(0.25)
    raise RuntimeError('Een node of het gedeelde proxyadres reageert niet.')


def request(host, port, target, body=None):
    connection = http.client.HTTPConnection(host, port, timeout=2)
    try:
        connection.request('POST' if body is not None else 'GET', target,
                           json.dumps(body) if body is not None else None,
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def relay(stack, port, target):
    class Forward(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.create_connection(target, timeout=3) as backend:
                    while True:
                        ready, _, _ = select.select([self.request, backend], [], [], 30)
                        if not ready:
                            return
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            destination = backend if source is self.request else self.request
                            destination.sendall(data)
            except OSError:
                return

    server = stack.enter_context(socketserver.ThreadingTCPServer(('127.0.0.1', port), Forward))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.shutdown)


def launch(stack, log, *command, env=None):
    with log.open('ab') as output:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)

    def stop():
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)  # pylint: disable=no-member
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)  # pylint: disable=no-member
                process.wait()

    stack.callback(stop)
    return process


def node_setup(stack, lab, bridge, index, keepalived, keep_env):
    suffix = 'a' if index == 0 else 'b'
    node = lab / suffix
    node.mkdir(mode=0o755)
    name = bridge + suffix
    address = f'{NETWORK}.{11 + index}'
    run('ip', 'netns', 'add', name)
    stack.callback(subprocess.run, ['ip', 'netns', 'delete', name], check=False)
    run('ip', 'link', 'add', name, 'type', 'veth', 'peer', 'name', 'eth0', 'netns', name)
    run('ip', 'link', 'set', name, 'master', bridge)
    run('ip', 'link', 'set', name, 'up')
    ns = ('ip', 'netns', 'exec', name)
    run(*ns, 'ip', 'link', 'set', 'lo', 'up')
    run(*ns, 'ip', 'link', 'set', 'eth0', 'up')
    run(*ns, 'ip', 'addr', 'add', address + '/24', 'dev', 'eth0')
    config, rules = node / 'tinyproxy.conf', node / 'filter.txt'
    ha = install.ha_topology(suffix, 'eth0', NETWORK + '.11', NETWORK + '.12',
                             VIP + '/24', NETWORK + '.0/24', 77)
    config.write_text(install.tinyproxy_text(rules, ha) + f'PidFile "{node}/proxy.pid"\n')
    rules.write_text(r'^198[.]18[.]77[.]1(:[0-9]+)?$' + '\n')
    keep = node / 'keepalived.conf'
    keep.write_text(install.keepalived_text(ha))
    run(*ns, keepalived, '--config-test', '-f', str(keep), env=keep_env)

    def start_proxy():
        return launch(stack, node / 'proxy.log', *ns, '/usr/bin/tinyproxy', '-d', '-c', str(config))

    proxy = start_proxy()
    launch(stack, node / 'keepalived.log', *ns, keepalived, '--dont-fork', '--vrrp', '--log-console',
           '-f', str(keep), '-p', str(node / 'keep.pid'), '-r', str(node / 'vrrp.pid'), env=keep_env)
    password, mfa_password = secrets.token_urlsafe(20), secrets.token_urlsafe(20)
    otp = pyotp.TOTP(pyotp.random_base32())
    accounts = {
        'lab-admin': {'password_hash': generate_password_hash(password), 'revision': secrets.token_hex(16)},
        'lab-mfa': {'password_hash': generate_password_hash(mfa_password), 'revision': secrets.token_hex(16),
                    'totp_secret': otp.secret},
    }
    install.atomic_write(node / 'accounts.json', json.dumps(accounts))
    install.atomic_write(node / 'logins.txt', f'lab-admin: {password}\nlab-mfa: {mfa_password}\nTOTP seed: {otp.secret}\n')
    env = dict(os.environ, FLASK_SECRET_KEY=secrets.token_hex(32), ENABLE_AUTH='true', ENABLE_RADIUS='false',
               AUTH_ACCOUNTS_FILE=str(node / 'accounts.json'), DEMO_MODE='true', DEPLOYMENT_MODE='ha',
               PROXYPERMIT_MANAGED_HA='true', PROXYPERMIT_HA_NODE=suffix, VIP_ADDRESS=VIP,
               NODE0_ADDRESS=NETWORK + '.11', NODE1_ADDRESS=NETWORK + '.12',
               APP_SERVER='granian', APP_BIND=address + ':8098', APP_WORKERS='1',
               GUNICORN_USE_TLS='false', MANAGER_SCHEME='http', SESSION_COOKIE_SECURE='false',
               SESSION_COOKIE_NAME=f'proxypermit_lab_{suffix}',
               TINYPROXY_CONFIG_PATH=str(config), WHITELIST_PATH=str(rules), SYNC_SECRET_FILE=str(node / 'sync.key'),
               SESSION_STATE_FILE=str(node / 'session.json'), NONCE_STORE_FILE=str(node / 'nonces.json'),
               AUDIT_LOG_PATH=str(node / 'audit.log'), WHITELIST_ENTRY_LOG_PATH=str(node / 'entries.json'))
    launch(stack, node / 'manager.log', *ns, sys.executable, '-m', 'app.runtime.server', env=env)
    port = 8101 + index
    relay(stack, port, (address, 8098))
    wait_for(lambda: request('127.0.0.1', port, '/')[0] == 200)
    login = {'username': 'lab-mfa', 'password': mfa_password}
    status, challenge = request('127.0.0.1', port, '/api/auth/login', login)
    if status != 401 or not json.loads(challenge).get('otpRequired'):
        raise RuntimeError('Het testaccount vroeg niet om de tweede factor.')
    login['otp'] = otp.now()
    if request('127.0.0.1', port, '/api/auth/login', login)[0] != 200:
        raise RuntimeError('Inloggen met de tweede factor is mislukt.')
    if request('127.0.0.1', port, '/api/auth/login', login)[0] != 401:
        raise RuntimeError('De authenticatorcode kon opnieuw worden gebruikt.')
    print(f'Node {suffix.upper()}: http://localhost:{port}/ · 2FA en replay gecontroleerd', flush=True)
    return ns, proxy, start_proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', type=Path, help='Optioneel pad naar een uitgepakt Ubuntu Keepalived-pakket.')
    parser.add_argument('--hold', action='store_true', help='Laat het lab na de proef draaien tot SIGTERM of Ctrl+C.')
    args = parser.parse_args()
    install.require_linux_root('Het HA-lab starten')
    if os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'):
        raise ValueError('Gebruik unshare --mount --propagation private voor het HA-lab.')
    bridge = 'pp' + secrets.token_hex(3)
    with ExitStack() as stack:
        lab = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='proxypermit-ha-lab-')))
        lab.chmod(0o711)
        keepalived, keep_env = 'keepalived', dict(os.environ)
        for name in ('upper', 'work'):
            (lab / name).mkdir()
        run('mount', '-t', 'overlay', 'overlay', '-o',
            f'lowerdir=/etc,upperdir={lab}/upper,workdir={lab}/work', '/etc')
        stack.callback(run, 'umount', '/etc')
        if args.package_root:
            (Path('/etc/keepalived')).mkdir(exist_ok=True)
            source = args.package_root / 'etc/keepalived/keepalived.config-opts'
            install.atomic_write('/etc/keepalived/keepalived.config-opts', source.read_text(), 0o644)
            keepalived = str(args.package_root / 'usr/sbin/keepalived')
            keep_env['LD_LIBRARY_PATH'] = str(args.package_root / 'usr/lib/x86_64-linux-gnu')
        install.atomic_write(install.KEEPALIVED_HEALTH, install.KEEPALIVED_HEALTH_TEXT, 0o755)
        run('ip', 'link', 'add', bridge, 'type', 'bridge')
        stack.callback(run, 'ip', 'link', 'delete', bridge)
        run('ip', 'addr', 'add', NETWORK + '.1/24', 'dev', bridge)
        run('ip', 'link', 'set', bridge, 'up')
        a, proxy_a, restart_a = node_setup(stack, lab, bridge, 0, keepalived, keep_env)
        b, _, _ = node_setup(stack, lab, bridge, 1, keepalived, keep_env)
        public = lab / 'public'
        public.mkdir()
        (public / 'probe.txt').write_text('ProxyPermit HA lab')
        launch(stack, lab / 'target.log', sys.executable, '-m', 'http.server', '8103',
               '--bind', NETWORK + '.1', '--directory', str(public))
        url = 'http://' + NETWORK + '.1:8103/probe.txt'
        def owns(ns):
            return VIP + '/' in run(*ns, 'ip', '-4', 'address', 'show', 'eth0', capture_output=True, text=True).stdout
        def forwarded():
            return request(VIP, 3128, url) == (200, b'ProxyPermit HA lab')
        wait_for(lambda: owns(a) and not owns(b) and forwarded())
        proxy_a.terminate()
        proxy_a.wait(timeout=5)
        start = time.monotonic()
        wait_for(lambda: owns(b) and not owns(a) and forwarded())
        print(f'HA: nieuwe HTTP-aanvraag via hetzelfde proxyadres na {time.monotonic() - start:.1f}s', flush=True)
        restart_a()
        relay(stack, 8130, (VIP, 3128))
        print(f'Testaccounts voor A en B staan in {lab}/a/logins.txt en {lab}/b/logins.txt (root).', flush=True)
        if args.hold:
            stopped = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            stopped.wait()


if __name__ == '__main__':
    main()
