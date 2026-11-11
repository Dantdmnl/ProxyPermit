"""Privileged standalone-to-HA transaction, invoked by a fixed systemd unit."""
import json
import os
import re
import secrets
import stat
import time
import hashlib
import socket
import ssl
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from app.core.ha import HA_SETUP_REQUEST, HA_SETUP_RESULT, HA_SETUP_ENV, ha_proxy_config, ha_setup_firewall_rules, ha_topology, setup_revision
from app.core.tinyproxy import TinyproxyConfigStore, atomic_write
from app.core.files import file_lock
from app.web import ha_replication
from scripts.deploy import install as deploy


TLS_SERVICE = Path('/etc/systemd/system/proxypermit-ha-tls.service')
POLICY_SERVICE = Path('/etc/systemd/system/proxypermit-ha-policy.service')
POLICY_TIMER = Path('/etc/systemd/system/proxypermit-ha-policy.timer')
TLS_FOLDER = Path('/etc/proxypermit/ha-tls')


@contextmanager
def frozen_proxy_directory(directory, lock_path):
    """Prevent an application-owned pathname swap during privileged recovery."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)  # pylint: disable=no-member
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0:
            raise ValueError('The Tinyproxy directory must be owned by root.')
        original = stat.S_IMODE(info.st_mode)
        os.fchmod(descriptor, original & ~0o022)  # pylint: disable=no-member
        try:
            if lock_path.is_symlink() or (lock_path.exists() and
                    (not lock_path.is_file() or lock_path.stat().st_nlink != 1)):
                raise ValueError('Invalid Tinyproxy lock file.')
            yield
        finally:
            os.fchmod(descriptor, original)  # pylint: disable=no-member
    finally:
        os.close(descriptor)


def tls_unit(base, local):
    return (
        '[Unit]\nDescription=Internal ProxyPermit HA TLS endpoint\nAfter=network-online.target\n'
        '[Service]\nUser=proxypermit\nGroup=proxypermit\nEnvironmentFile=/etc/proxypermit/.env\n'
        f'WorkingDirectory={base}/current\nExecStart={base}/current/.venv/bin/python -m gunicorn '
        f'--bind {local}:8443 --workers 1 --access-logfile /dev/null '
        '--certfile /etc/proxypermit/ha-tls/cert.pem --keyfile /etc/proxypermit/ha-tls/key.pem '
        'app.runtime.ha_wsgi:application\nRestart=on-failure\nUMask=0077\n'
        'NoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\n'
        '[Install]\nWantedBy=multi-user.target\n'
    )


def read_request():
    fd = os.open(HA_SETUP_REQUEST, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # pylint: disable=no-member
    with os.fdopen(fd, 'r', encoding='utf-8') as handle:
        info = os.fstat(handle.fileno())
        uid, _, _ = deploy.service_ownership()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077 or info.st_size > 8192:
            raise ValueError('Invalid HA setup request ownership, permissions, or size.')
        request = json.load(handle)
    if not isinstance(request, dict) or not re.fullmatch(r'[0-9a-f]{32}', request.get('id', '')):
        raise ValueError('Invalid HA setup request.')
    raw = request['topology']
    if (not isinstance(raw, dict) or type(raw.get('vrid')) is not int or
            any(not isinstance(raw.get(key), str) or len(raw[key]) > 64
                for key in ('node', 'interface', 'nodeA', 'nodeB', 'vip', 'clients'))
            or type(request.get('created')) is not int or not time.time() - 300 < request['created'] <= time.time() + 30):
        raise ValueError('Invalid or expired HA setup request.')
    topology = ha_topology(*(raw[key] for key in ('node', 'interface', 'nodeA', 'nodeB', 'vip', 'clients', 'vrid')))
    return request, topology


def result(request_id, phase, backup=None, error=None):
    atomic_write(HA_SETUP_RESULT, json.dumps(dict(id=request_id, phase=phase, backup=backup, error=error)), 0o644)


def verify_unused_vip(topology):
    if topology['node'] != 'a':
        return
    # A probe detects responding owners; the operator must still reserve the IP
    # in DHCP/IPAM to prevent an independent allocation after this check.
    probe = deploy.subprocess.run(['arping', '-D', '-q', '-I', topology['interface'],
                                   '-c', '3', '-w', '4', topology['vip'].split('/')[0]],
                                  check=False, capture_output=True, timeout=6)
    if probe.returncode != 0:
        raise ValueError('The virtual IP is in use or could not be checked. Reserve another address and review the plan.')


def reset_standby_history(peer_file):
    # Standalone recovery can leave history from an earlier membership.
    for name in ('ha-applied-policy.json', 'ha-replication-status.json'):
        peer_file.with_name(name).unlink(missing_ok=True)


def configure(base):
    deploy.require_linux_root('HA setup')
    request = None
    try:
        request, topology = read_request()
        result(request['id'], 'running')
        with deploy.maintenance_lock():
            _configure_locked(Path(base), request, topology)
    except (Exception, KeyboardInterrupt) as exc:
        previous = json.loads(HA_SETUP_RESULT.read_text()) if HA_SETUP_RESULT.is_file() else {}
        result(request['id'] if request else previous.get('id'), 'failed', previous.get('backup'),
               str(exc) if isinstance(exc, ValueError) else 'HA setup failed. Check the host service log.')
        raise
    finally:
        HA_SETUP_REQUEST.unlink(missing_ok=True)


def _configure_locked(base, request, topology):
    installed, _, _, _ = deploy.installed_deployment('/', str(base))
    deploy.verify_live_manager(str(base))
    if installed.get('mode') != 'standalone':
        raise ValueError('Only an installer-managed standalone node can be converted.')
    values = deploy.env_values(HA_SETUP_ENV)
    deploy.secure_ha_helper_source(base, (base / 'current').resolve())
    if values.get('DEPLOYMENT_MODE') != 'standalone':
        raise ValueError('The installed deployment mode changed.')
    config = Path(values.get('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))
    filters = Path(values.get('WHITELIST_PATH', '/etc/tinyproxy/whitelist.txt'))
    if any(path.parent != Path('/etc/tinyproxy') or path.is_symlink() for path in (config, filters)):
        raise ValueError('HA conversion requires regular Tinyproxy files under /etc/tinyproxy.')
    with frozen_proxy_directory(config.parent, Path(str(filters) + '.lock')), file_lock(str(filters) + '.lock'):
        if any(path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 for path in (config, filters)):
            raise ValueError('HA conversion requires regular, unlinked Tinyproxy files.')
        if any(TinyproxyConfigStore(path).read()['staged'] for path in (config, filters)):
            raise ValueError('Publish or discard saved drafts before HA setup.')
        if request.get('revision') != setup_revision(config, filters):
            raise ValueError('The installation changed since the HA plan was reviewed.')
        conflicts = (deploy.KEEPALIVED_CONFIG, deploy.KEEPALIVED_HEALTH, deploy.HA_FIREWALL_STATE,
                     TLS_FOLDER, TLS_SERVICE, POLICY_SERVICE, POLICY_TIMER)
        if any(path.exists() or path.is_symlink() for path in conflicts):
            raise ValueError('Existing HA files require operator review before conversion.')
        peer_file = Path(values.get('SESSION_STATE_FILE', '/var/lib/proxypermit/session-state.json')).with_name('ha-peer.json')
        if peer_file.exists() or peer_file.is_symlink():
            raise ValueError('Existing HA files require operator review before conversion.')
        if deploy.service_state('keepalived.service')['active']:
            raise ValueError('Keepalived is already active on this host.')
        interfaces = json.loads(deploy.run('ip', '-j', '-4', 'address', 'show', 'dev', topology['interface'],
                                            capture_output=True, text=True).stdout)
        if not any(address['local'] == topology['local'] and not address.get('dynamic')
                   for interface in interfaces for address in interface.get('addr_info', [])):
            raise ValueError('The selected interface does not have the local HA address.')
        updated = ha_proxy_config(config.read_text(encoding='utf-8-sig'), topology)
        firewall = deploy.run('ufw', 'status', 'verbose', capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'}).stdout
        if not re.search(r'^Status: active$', firewall, re.M) or 'deny (incoming)' not in firewall:
            raise ValueError('Enable UFW with default incoming deny and allow management access before HA conversion.')
        logging_dropin = Path('/etc/systemd/system/keepalived.service.d/20-proxypermit-logging.conf')
        logging_content = None
        if values.get('PROXYPERMIT_MANAGED_LOGGING') == 'true':
            logging_content = deploy.logging_assets(base / 'current', {**values, 'DEPLOYMENT_MODE': 'ha'})[logging_dropin]
            if logging_dropin.is_symlink() or (logging_dropin.is_file() and not logging_dropin.read_text().startswith(deploy.LOGGING_MARKER)):
                raise ValueError('An administrator-owned logging policy requires review.')
        services = {name: deploy.service_state(name) for name in
                    ('tinyproxy.service', 'keepalived.service', 'proxypermit-ha-tls.service', 'proxypermit-ha-policy.timer')}
        try:
            deploy.run('apt-get', '-o', 'DPkg::Lock::Timeout=60', 'install', '-y', 'keepalived', 'ufw', 'openssl', 'iputils-arping')
            verify_unused_vip(topology)
        except (Exception, KeyboardInterrupt):
            deploy.restore_auxiliary_services(services)
            raise
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        transaction = deploy.Transaction(base / 'backups' / stamp)
        snapshot = dict(**deploy.service_state('proxypermit.service'), auxiliary=services, ufw=True)
        paths = (config, HA_SETUP_ENV, base / 'installed.json', deploy.KEEPALIVED_CONFIG, deploy.KEEPALIVED_HEALTH,
                 deploy.HA_FIREWALL_STATE, deploy.HA_RETIRE_UNIT, deploy.HA_RETIRE_SUDOERS,
                 TLS_SERVICE, POLICY_SERVICE, POLICY_TIMER, TLS_FOLDER, TLS_FOLDER / 'cert.pem', TLS_FOLDER / 'key.pem')
        for path in (*paths, *deploy.UFW_RULE_FILES):
            transaction.track(path)
        if topology['node'] == 'b':
            transaction.track_new(peer_file)
        if logging_content:
            transaction.track(logging_dropin)
        atomic_write(transaction.directory / 'service-state.json', json.dumps(snapshot))
        result(request['id'], 'running', stamp)
        print(f'Recovery backup: {stamp}', flush=True)
        uid, gid, chown = deploy.service_ownership()
        peer_written = False
        try:
            rules = ha_setup_firewall_rules(topology)
            owned = deploy.apply_ha_firewall(rules)
            atomic_write(deploy.HA_FIREWALL_STATE, json.dumps(dict(topology=topology, rules=owned)))
            snapshot['ufwRuleHashes'] = deploy.ufw_rule_fingerprints()
            atomic_write(transaction.directory / 'service-state.json', json.dumps(snapshot))
            if topology['node'] == 'b':
                os.environ.update(NODE0_ADDRESS=topology['nodeA'], NODE1_ADDRESS=topology['nodeB'], VIP_ADDRESS=topology['vip'].split('/')[0])
                invite = ha_replication.parse_pairing_code(request.get('joinCode'))
                enrolled = ha_replication.fetch_primary(invite['pin'], invite['key'], 'enroll')
                key = enrolled.get('key') if isinstance(enrolled, dict) else None
                if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
                    raise ValueError('The primary did not issue a valid peer credential.')
                remote = enrolled.get('topology')
                if not isinstance(remote, dict) or any(remote.get(name) != topology[name] for name in ('nodeA', 'nodeB', 'vip', 'clients', 'vrid')):
                    raise ValueError('The primary HA topology differs from the reviewed pairing code.')
            atomic_write(config, updated, config.stat().st_mode & 0o777)
            atomic_write(deploy.KEEPALIVED_CONFIG, deploy.keepalived_text(topology), 0o640)
            atomic_write(deploy.KEEPALIVED_HEALTH, deploy.KEEPALIVED_HEALTH_TEXT, 0o755)
            deploy.run('keepalived', '--config-test', '-f', str(deploy.KEEPALIVED_CONFIG))
            additions = dict(DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='true', PROXYPERMIT_HA_NODE=topology['node'],
                             NODE0_ADDRESS=topology['nodeA'], NODE1_ADDRESS=topology['nodeB'],
                             VIP_ADDRESS=topology['vip'].split('/')[0], PROXYPERMIT_HA_INTERFACE=topology['interface'],
                             HA_CLIENT_CIDR=topology['clients'], HA_VRID=str(topology['vrid']),
                             HA_VIP_PREFIX=topology['vip'].split('/')[1])
            if topology['node'] == 'a':
                TLS_FOLDER.mkdir(mode=0o750)
                TLS_FOLDER.chmod(0o750)
                chown(TLS_FOLDER, 0, gid)
                deploy.run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-days', '365', '-nodes',
                           '-subj', '/CN=ProxyPermit-HA', '-keyout', str(TLS_FOLDER / 'key.pem'), '-out', str(TLS_FOLDER / 'cert.pem'),
                           capture_output=True)
                for name in ('cert.pem', 'key.pem'):
                    (TLS_FOLDER / name).chmod(0o640)
                    chown(TLS_FOLDER / name, 0, gid)
                certificate = deploy.run('openssl', 'x509', '-in', str(TLS_FOLDER / 'cert.pem'), '-outform', 'DER', capture_output=True).stdout
                additions['HA_POLICY_TLS_PIN'] = hashlib.sha256(certificate).hexdigest()
                atomic_write(TLS_SERVICE, tls_unit(base, topology['local']), 0o644)
            else:
                service = (base / 'current/deploy/systemd/proxypermit-ha-policy.service').read_text().replace('/opt/proxypermit/', f'{base}/')
                atomic_write(POLICY_SERVICE, service, 0o644)
                atomic_write(POLICY_TIMER, (base / 'current/deploy/systemd/proxypermit-ha-policy.timer').read_text(), 0o644)
            env = HA_SETUP_ENV.read_text().rstrip() + '\n' + ''.join(f'{key}={value}\n' for key, value in additions.items())
            atomic_write(HA_SETUP_ENV, env, 0o640)
            atomic_write(deploy.HA_RETIRE_UNIT, deploy.ha_retire_unit_text(str(base)), 0o644)
            atomic_write(deploy.HA_RETIRE_SUDOERS, deploy.HA_RETIRE_RULE, 0o440)
            deploy.run('visudo', '-cf', str(deploy.HA_RETIRE_SUDOERS))
            if logging_content:
                atomic_write(logging_dropin, logging_content, 0o644)
            deploy.run('systemctl', 'daemon-reload')
            deploy.run('systemctl', 'restart', 'tinyproxy.service')
            deploy.start_ha()
            if topology['node'] == 'b':
                reset_standby_history(peer_file)
                peer_written = True
                atomic_write(peer_file, json.dumps(dict(pin=invite['pin'], key=key)), 0o640)
                with os.fdopen(os.open(peer_file, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as handle:  # pylint: disable=no-member
                    os.fchown(handle.fileno(), uid, gid)  # pylint: disable=no-member
            installed.update(mode='ha', backup=str(transaction.directory))
            atomic_write(base / 'installed.json', json.dumps(installed))
            result(request['id'], 'activating', stamp)
            deploy.run('systemctl', 'restart', 'proxypermit.service')
            for _ in range(20):
                if deploy.probe_manager(deploy.env_values(HA_SETUP_ENV)):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError('The management console did not recover after HA setup.')
            deploy.run('systemctl', 'enable', '--now', 'proxypermit-ha-tls.service' if topology['node'] == 'a' else 'proxypermit-ha-policy.timer')
            if topology['node'] == 'a':
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                for _ in range(20):
                    try:
                        with socket.create_connection((topology['local'], 8443), timeout=1) as connection:
                            with context.wrap_socket(connection, server_hostname=topology['local']) as tls:
                                pin = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
                                if pin != additions['HA_POLICY_TLS_PIN']:
                                    raise ValueError('Another TLS service is listening on the HA address.')
                        break
                    except OSError:
                        time.sleep(0.5)
                else:
                    raise RuntimeError('The internal HA TLS endpoint did not start.')
            result(request['id'], 'complete', stamp)
        except (Exception, KeyboardInterrupt):
            for name in ('keepalived.service', 'proxypermit-ha-tls.service', 'proxypermit-ha-policy.timer'):
                deploy.subprocess.run(['systemctl', 'stop', name], check=False)
            if peer_written:
                peer_file.unlink(missing_ok=True)
            transaction.restore()
            if TLS_FOLDER.is_dir() and not any(TLS_FOLDER.iterdir()):
                TLS_FOLDER.rmdir()
            deploy.run('systemctl', 'daemon-reload')
            deploy.reload_ufw()
            deploy.restore_auxiliary_services(services)
            deploy.run('systemctl', 'restart', 'proxypermit.service')
            raise
