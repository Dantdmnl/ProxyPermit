"""Root-operated HA leaf-certificate renewal with an irreversible commit record.

Stage through the existing pinned connection, then commit only after B has saved
the candidate. Management can restart; Tinyproxy and Keepalived are untouched.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import subprocess
import sys
import time

from app.core.files import file_lock, sync_directory
from app.core.ha_credentials import valid_key
from app.core import ha_certificates as certificates
from app.core.tinyproxy import atomic_write, ConfigError
from app.web import ha_replication
from scripts.deploy import install as deploy

FOLDER = Path('/etc/proxypermit/ha-tls')
ENVIRONMENT = Path('/etc/proxypermit/.env')
RECOVERY_UNIT = deploy.HA_CERT_RECOVERY_UNIT
BASE = Path(__file__).resolve().parents[2]


def run(*args, cwd=None, env=None):
    return subprocess.run(args, check=True, capture_output=True, timeout=30, cwd=cwd, env=env)


def protected(path, maximum=16384):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > maximum):
            raise ConfigError('Certificate maintenance requires protected root-owned files.')
        return source.read()


def root_write(path, content, mode=0o600, gid=0):
    atomic_write(path, content, mode)
    os.chown(path, 0, gid)  # pylint: disable=no-member
    with path.open('rb') as source:
        os.fsync(source.fileno())
    sync_directory(path.parent)


def pin(path):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(protected(path).decode('ascii'))).hexdigest()


def check_pair(cert, key):
    run('openssl', 'x509', '-in', str(cert), '-checkend', '0', '-noout')
    certificate = run('openssl', 'x509', '-in', str(cert), '-pubkey', '-noout').stdout
    private = run('openssl', 'pkey', '-in', str(key), '-pubout').stdout
    if certificate != private:
        raise ConfigError('The HA certificate and private key do not match.')


def peer_state():
    path = ha_replication.state('peer')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
            raise ConfigError('Invalid peer state.')
        value = json.load(source)
    if not isinstance(value, dict) or not valid_key(value.get('key')):
        raise ConfigError('Pair the nodes before renewing the HA certificate.')
    return value


def key_binding(peer):
    return hashlib.sha256(peer['key'].encode('ascii')).hexdigest()


def actor_uid():
    raw = os.getenv('SUDO_UID', '')
    return int(raw) if raw.isascii() and raw.isdecimal() else os.getuid()  # pylint: disable=no-member


def audit(event, plan):
    run('runuser', '-u', 'proxypermit', '--', str(BASE / '.venv/bin/python'),
        '-m', 'scripts.tools.record_maintenance', event, plan['id'], str(actor_uid()), cwd=BASE,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})


def no_key_rotation(peer):
    rotation = peer.get('rotation') or {}
    if not isinstance(rotation, dict) or rotation.get('state') in ('prepared', 'committed'):
        raise ConfigError('Finish the peer-key rotation before certificate maintenance.')


def public(plan):
    return {key: plan[key] for key in ('id', 'oldPin', 'newPin', 'created', 'expires')} | {
        'phase': 'offered' if plan['phase'] == 'offered' else 'committed'}


def install_recovery(base):
    text = deploy.ha_certificate_recovery_unit_text(base)
    root_write(RECOVERY_UNIT, text, 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', RECOVERY_UNIT.name)


def load_plan(private):
    path = private / 'plan.json'
    if not path.exists():
        return None
    plan = json.loads(protected(path))
    if (not isinstance(plan, dict) or not isinstance(plan.get('id'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', plan['id'])
            or plan.get('phase') not in ('offered', 'committed', 'complete')
            or plan.get('service') not in ('proxypermit-ha-tls.service', 'nginx.service')
            or not valid_key(plan.get('binding'))):
        raise ConfigError('Invalid certificate recovery plan.')
    certificates.validate(public(plan))
    return plan


def write_public(plan, gid):
    root_write(ha_replication.CERTIFICATE_TRANSITION, json.dumps(public(plan)), 0o640, gid)


def stage(private, values, service, base, gid):
    current = load_plan(private)
    if current and current['phase'] != 'complete':
        raise ConfigError('An HA certificate transition already exists. Complete or cancel it first.')
    peer = peer_state()
    no_key_rotation(peer)
    if peer.get('certificateProtocol') != 1 or not 0 <= time.time() - peer.get('credentialChecked', 0) < 90:
        raise ConfigError('Upgrade both nodes and wait for successful synchronization first.')
    old_pin = pin(FOLDER / 'cert.pem')
    if values.get('HA_POLICY_TLS_PIN') != old_pin:
        raise ConfigError('The active HA certificate does not match the configured pin.')
    check_pair(FOLDER / 'cert.pem', FOLDER / 'key.pem')
    run('openssl', 'x509', '-in', str(FOLDER / 'cert.pem'), '-checkend', '660', '-noout')
    run('systemctl', 'is-active', '--quiet', service)
    local = str(ipaddress.IPv4Address(values['NODE0_ADDRESS']))
    identifier = secrets.token_hex(16)
    directory = private / identifier
    directory.mkdir(mode=0o700)
    for name in ('cert.pem', 'key.pem'):
        root_write(directory / ('before-' + name), protected(FOLDER / name).decode('ascii'))
    root_write(directory / 'before-env', protected(ENVIRONMENT, 1024 * 1024).decode('utf-8'))
    run('openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes', '-days', '365',
        '-subj', '/CN=ProxyPermit-HA', '-addext', 'subjectAltName=IP:' + local,
        '-addext', 'basicConstraints=critical,CA:FALSE', '-addext', 'extendedKeyUsage=serverAuth',
        '-keyout', str(directory / 'key.pem'), '-out', str(directory / 'cert.pem'))
    for name in ('key.pem', 'cert.pem'):
        (directory / name).chmod(0o600)
        with (directory / name).open('rb') as source:
            os.fsync(source.fileno())
    sync_directory(directory)
    sync_directory(private)
    now = int(time.time())
    plan = dict(id=identifier, oldPin=old_pin, newPin=pin(directory / 'cert.pem'),
                created=now, expires=now + 600, phase='offered', binding=key_binding(peer), service=service,
                initiatedByUid=actor_uid())
    root_write(private / 'plan.json', json.dumps(plan))
    install_recovery(base)
    write_public(plan, gid)
    # Drain old browser workers before checking overlap. The internal machine
    # listener cannot start key rotation; new browser workers see the root offer.
    run('systemctl', 'restart', 'proxypermit.service')
    no_key_rotation(peer_state())
    audit('ha_certificate_staged', plan)
    print('HA certificate staged. Run activate within ten minutes after B has synchronized.')


def finish(private, plan, gid, restart=True):
    directory = private / plan['id']
    check_pair(directory / 'cert.pem', directory / 'key.pem')
    if pin(directory / 'cert.pem') != plan['newPin'] or pin(FOLDER / 'cert.pem') not in (plan['oldPin'], plan['newPin']):
        raise ConfigError('Certificate recovery conflicts with the current host configuration.')
    environment = protected(ENVIRONMENT, 1024 * 1024).decode('utf-8')
    current_pin = deploy.env_values(ENVIRONMENT).get('HA_POLICY_TLS_PIN')
    if current_pin not in (plan['oldPin'], plan['newPin']):
        raise ConfigError('The configured HA pin changed during certificate recovery.')
    for name in ('cert.pem', 'key.pem'):
        root_write(FOLDER / name, protected(directory / name).decode('ascii'), 0o640, gid)
    if current_pin != plan['newPin']:
        root_write(ENVIRONMENT, environment.rstrip() + '\n\n# HA certificate renewal\nHA_POLICY_TLS_PIN=' + plan['newPin'] + '\n', 0o640, gid)
    write_public(plan, gid)
    if restart:
        if plan['service'] == 'nginx.service':
            run('nginx', '-t')
            run('systemctl', 'reload', plan['service'])
        else:
            run('systemctl', 'restart', plan['service'])
        run('systemctl', 'restart', 'proxypermit.service')


def activate(private, gid):
    plan = load_plan(private)
    if not plan:
        raise ConfigError('Stage a certificate first.')
    if plan['phase'] == 'complete':
        print('HA certificate renewal is already complete.')
        return
    if (plan['phase'] == 'committed' and peer_state().get('certificateVersion') == plan['id']
            and pin(FOLDER / 'cert.pem') == plan['newPin']
            and deploy.env_values(ENVIRONMENT).get('HA_POLICY_TLS_PIN') == plan['newPin']):
        root_write(private / 'plan.json', json.dumps({**plan, 'phase': 'complete'}))
        audit('ha_certificate_completed', plan)
        print('HA certificate renewal confirmed by B.')
        return
    if plan['phase'] == 'offered':
        peer = peer_state()
        no_key_rotation(peer)
        if (time.time() >= plan['expires'] - 60 or peer.get('certificatePrepared') != plan['id']
                or key_binding(peer) != plan['binding']):
            raise ConfigError('B has not durably acknowledged the candidate, the peer changed, or the transition is expiring.')
        audit('ha_certificate_commit_requested', plan)
        plan = {**plan, 'phase': 'committed', 'committedByUid': actor_uid()}
        # The durable decision precedes replacing either active file. From this
        # point recovery finishes forward; it must never silently restore old pins.
        root_write(private / 'plan.json', json.dumps(plan))
        audit('ha_certificate_committed', plan)
    finish(private, plan, gid)
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        peer = peer_state()
        if peer.get('certificateVersion') == plan['id'] and key_binding(peer) == plan['binding']:
            root_write(private / 'plan.json', json.dumps({**plan, 'phase': 'complete'}))
            audit('ha_certificate_completed', plan)
            print('HA certificate renewed and confirmed by B. The old pin is no longer trusted by B.')
            return
        time.sleep(.5)
    raise ConfigError('The new certificate is committed; B has not confirmed it yet. Retry activate or review recovery. Do not restore the old certificate.')


def cancel(private):
    plan = load_plan(private)
    if plan and plan['phase'] != 'offered':
        raise ConfigError('A committed certificate renewal cannot be cancelled. Finish forward.')
    if plan:
        audit('ha_certificate_cancelled', plan)
    ha_replication.CERTIFICATE_TRANSITION.unlink(missing_ok=True)
    sync_directory(ha_replication.CERTIFICATE_TRANSITION.parent)
    (private / 'plan.json').unlink(missing_ok=True)
    sync_directory(private)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('plan', 'stage', 'activate', 'recover', 'cancel'), default='plan')
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    parser.add_argument('--tls-service', choices=('proxypermit-ha-tls.service', 'nginx.service'), default='proxypermit-ha-tls.service')
    parser.add_argument('--no-restart', action='store_true', help='Recovery unit only: finish files before services start.')
    args = parser.parse_args(argv)
    if args.action == 'plan':
        print('Stage a unique HA certificate, wait for B, then commit and restart internal TLS and management. Proxy traffic services are preserved.')
        return 0
    try:
        deploy.require_linux_root('HA certificate maintenance')
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', args.install_dir) or '..' in Path(args.install_dir).parts:
            raise ConfigError('Use the existing installer-managed installation directory.')
        protected(Path(args.install_dir) / 'installed.json', 1024 * 1024)
        global BASE
        BASE = Path(args.install_dir) / 'current'
        protected(ENVIRONMENT, 1024 * 1024)
        values = deploy.env_values(ENVIRONMENT)
        if values.get('PROXYPERMIT_HA_NODE') != 'a' or values.get('PROXYPERMIT_MANAGED_HA') != 'true':
            raise ConfigError('Run certificate maintenance on the managed primary HA node.')
        os.environ.update(values)
        private = FOLDER / 'maintenance'
        if FOLDER.is_symlink() or FOLDER.stat().st_uid != 0 or FOLDER.stat().st_mode & 0o022:
            raise ConfigError('The HA TLS directory must be protected by root.')
        private.mkdir(mode=0o700, exist_ok=True)
        if private.is_symlink() or private.stat().st_uid != 0 or private.stat().st_mode & 0o077:
            raise ConfigError('The certificate maintenance directory must be private to root.')
        _, gid, _ = deploy.service_ownership()
        with deploy.maintenance_lock(), file_lock(private / 'maintenance.lock'):
            if args.action == 'stage':
                stage(private, values, args.tls_service, Path(args.install_dir), gid)
            elif args.action == 'activate':
                activate(private, gid)
            elif args.action == 'recover':
                plan = load_plan(private)
                if plan and plan['phase'] == 'committed':
                    finish(private, plan, gid, restart=not args.no_restart)
                    audit('ha_certificate_recovered', plan)
            elif args.action == 'cancel':
                cancel(private)
                print('Uncommitted HA certificate offer cancelled; the active certificate was preserved.')
        return 0
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('HA certificate maintenance stopped. Check the root recovery plan and service status; certificate verification remains enabled.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
