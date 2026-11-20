"""Opt-in, root-operated management HTTPS with a stable node CA and recoverable renewal.

Only the management service restarts. Native proxy policy, VIP ownership and the
internal HA certificate/pin are outside this module's write set.
"""
import argparse
import hashlib
import http.client
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
from app.core.tinyproxy import ConfigError, atomic_write
from scripts.deploy import install as deploy

FOLDER = Path('/etc/proxypermit/management-tls')
ENVIRONMENT = Path('/etc/proxypermit/.env')
MARKER = '# Managed by ProxyPermit management TLS\n'
UNIT = 'proxypermit.service'
RENEW = 'proxypermit-management-tls-renew'
RECOVER = 'proxypermit-management-tls-recover.service'
DAYS, RENEW_BEFORE = 90, 30


def run(*args, cwd=None):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}, cwd=cwd)


def protected(path, maximum=1024 * 1024, private=False):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > maximum or (private and info.st_mode & 0o077)):
            raise ConfigError('Management TLS requires protected root-owned files.')
        return source.read()


def directory(path, gid=0):
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o750)
        os.chown(path, 0, gid)  # pylint: disable=no-member
        path.chmod(0o750)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ConfigError('Management TLS directories must be protected by root.')


def write(path, content, mode=0o600, gid=0):
    atomic_write(path, content, mode)
    os.chown(path, 0, gid)  # pylint: disable=no-member
    with path.open('rb') as source:
        os.fsync(source.fileno())
    sync_directory(path.parent)


def names(dns=(), ips=()):
    result = {'DNS:localhost', 'IP:127.0.0.1', 'IP:::1'}
    for name in dns:
        if (not isinstance(name, str) or len(name) > 253
                or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                       for label in name.split('.'))):
            raise ConfigError('Use explicit DNS names without wildcards for management TLS.')
        result.add('DNS:' + name.lower())
    for address in ips:
        value = ipaddress.ip_address(address)
        if value.is_unspecified or value.is_multicast:
            raise ConfigError('Use a specific unicast management IP address.')
        result.add('IP:' + str(value))
    return sorted(result)


def rewrite_environment(content, updates):
    remaining = dict(updates)
    result = []
    for line in content.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=', line)
        if match and match[1] in updates:
            if match[1] in remaining:
                result.append(match[1] + '=' + remaining.pop(match[1]))
        else:
            result.append(line)
    return '\n'.join(result + [key + '=' + value for key, value in remaining.items()]) + '\n'


def certificate(path):
    text = protected(path, 16384)
    expires = run('openssl', 'x509', '-in', str(path), '-noout', '-enddate').stdout.strip().removeprefix('notAfter=')
    return dict(fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(text)).hexdigest(),
                expires=int(ssl.cert_time_to_seconds(expires)))


def check_pair(cert, key):
    protected(cert, 16384)
    protected(key, 16384)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)


def generate(sans, gid):
    ca, ca_key = FOLDER / 'ca.pem', FOLDER / 'ca-key.pem'
    if not ca.exists() and not ca.is_symlink() and not ca_key.exists() and not ca_key.is_symlink():
        run('openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
            '-nodes', '-days', '3650', '-sha256', '-subj', '/CN=ProxyPermit node management CA',
            '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
            '-keyout', str(ca_key), '-out', str(ca))
        ca_key.chmod(0o600)
        ca.chmod(0o644)
        for path in (ca_key, ca):
            with path.open('rb') as source:
                os.fsync(source.fileno())
        sync_directory(FOLDER)
    check_pair(ca, ca_key)
    protected(ca_key, 16384, private=True)
    if certificate(ca)['expires'] <= time.time() + (DAYS + 1) * 86400:
        raise ConfigError('The node management CA needs an explicit trust migration before leaf renewal.')
    identifier = secrets.token_hex(16)
    target = FOLDER / identifier
    directory(target, gid)
    extension = target / 'extensions.cnf'
    write(extension, 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
          'extendedKeyUsage=serverAuth\nsubjectAltName=' + ','.join(sans) + '\n')
    run('openssl', 'req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
        '-nodes', '-subj', '/CN=ProxyPermit management', '-keyout', str(target / 'key.pem'),
        '-out', str(target / 'request.pem'))
    run('openssl', 'x509', '-req', '-in', str(target / 'request.pem'), '-CA', str(ca), '-CAkey', str(ca_key),
        '-set_serial', '0x' + secrets.token_hex(16), '-days', str(DAYS), '-sha256',
        '-extfile', str(extension), '-out', str(target / 'cert.pem'))
    for name, mode in (('cert.pem', 0o644), ('key.pem', 0o640)):
        path = target / name
        os.chown(path, 0, gid)  # pylint: disable=no-member
        path.chmod(mode)
        with path.open('rb') as source:
            os.fsync(source.fileno())
    check_pair(target / 'cert.pem', target / 'key.pem')
    run('openssl', 'verify', '-CAfile', str(ca), '-purpose', 'sslserver', '-verify_ip', '127.0.0.1', str(target / 'cert.pem'))
    sync_directory(target)
    result = dict(version=1, generation=identifier, sans=sans, **certificate(target / 'cert.pem'),
                  caFingerprint=certificate(ca)['fingerprint'], caExpires=certificate(ca)['expires'])
    write(target / 'info.json', json.dumps(result), 0o644)
    return result


def load_profile():
    path = FOLDER / 'profile.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = json.loads(protected(path, 16384))
    if (not isinstance(value, dict) or value.get('version') != 1
            or not re.fullmatch(r'[0-9a-f]{32}', str(value.get('generation', '')))
            or not isinstance(value.get('sans'), list)
            or any(not isinstance(item, str) or not item.startswith(('DNS:', 'IP:')) for item in value['sans'])
            or value['sans'] != names([item[4:] for item in value['sans'] if item.startswith('DNS:')],
                                     [item[3:] for item in value['sans'] if item.startswith('IP:')])):
        raise ConfigError('Invalid management TLS profile. Review the root configuration.')
    return value


def endpoint(values):
    bind = values.get('APP_BIND') or values.get('GUNICORN_BIND') or f"{values.get('MANAGER_BIND', '127.0.0.1')}:{values.get('MANAGER_PORT', '8080')}"
    host, separator, port = bind.rpartition(':')
    if bind.startswith('unix:') or ',' in bind or not separator or not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ConfigError('Management TLS requires one existing TCP listener; its address will be preserved.')
    host = host.strip('[]')
    return {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host), int(port)


def probe(values, profile):
    host, port = endpoint(values)
    context = ssl.create_default_context(cafile=str(FOLDER / 'ca.pem'))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(host, port, timeout=2, context=context)
    try:
        connection.connect()
        if hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest() != profile['fingerprint']:
            return False
        connection.request('GET', '/')
        response = connection.getresponse()
        response.read(1024 * 1024)
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def assets(base):
    command = str(base / '.venv/bin/python') + ' -m scripts.deploy.management_tls'
    return {
        Path('/etc/systemd/system') / (RENEW + '.service'): MARKER + (
            '[Unit]\nDescription=Renew the node management HTTPS certificate\nAfter=network.target\n'
            '[Service]\nType=oneshot\nUser=root\nUMask=0077\nLogNamespace=proxypermit\n'
            f'WorkingDirectory={base}\nExecStart={command} renew --apply --install-dir {base.parent}\nTimeoutStartSec=180\n'),
        Path('/etc/systemd/system') / (RENEW + '.timer'): MARKER + (
            '[Unit]\nDescription=Check management certificate expiry daily\n'
            '[Timer]\nOnCalendar=daily\nRandomizedDelaySec=1h\nPersistent=true\n[Install]\nWantedBy=timers.target\n'),
        Path('/etc/systemd/system') / RECOVER: MARKER + (
            '[Unit]\nDescription=Recover interrupted management TLS activation\nAfter=local-fs.target\n'
            '[Service]\nType=oneshot\nRemainAfterExit=yes\nUser=root\nUMask=0077\nLogNamespace=proxypermit\n'
            f'WorkingDirectory={base}\nExecStart={command} recover --files-only --apply --install-dir {base.parent}\nTimeoutStartSec=90\n'),
        Path('/etc/systemd/system/proxypermit.service.d/30-management-tls.conf'): MARKER + (
            f'[Unit]\nRequires={RECOVER}\nAfter={RECOVER}\n')}


def audit(event, operation, base):
    uid = operation['actor']
    attribution = ['--requested-by=' + operation['requestedBy'], '--request-id=' + operation['requestId']] if operation.get('requestedBy') else []
    run('runuser', '-u', 'proxypermit', '--', str(base / '.venv/bin/python'),
        '-m', 'scripts.tools.record_maintenance', event, operation['id'], str(uid), *attribution, cwd=base)


def rollback_values(entries):
    """An app rollback must retain the current node trust root and leaf binding."""
    profile = load_profile() if FOLDER.exists() or FOLDER.is_symlink() else None
    if not profile:
        return None
    current = next((item for item in entries if Path(item['path']).name == 'current' and 'symlink' in item), None)
    if not current or not (Path(current['symlink']) / 'scripts/deploy/management_tls.py').is_file():
        raise ConfigError('This backup predates managed HTTPS. Review TLS recovery before restoring it.')
    values = deploy.manager_values(ENVIRONMENT, Path('/etc/systemd/system/proxypermit.service'))
    expected = FOLDER / profile['generation']
    if values.get('MANAGER_CERT') != str(expected / 'cert.pem') or values.get('MANAGER_KEY') != str(expected / 'key.pem'):
        raise ConfigError('The current management TLS binding needs OS review before rollback.')
    return {key: values[key] for key in ('MANAGER_SCHEME', 'GUNICORN_USE_TLS', 'SESSION_COOKIE_SECURE',
            'MANAGER_CERT', 'MANAGER_KEY', 'ENABLE_HSTS', 'HSTS_MAX_AGE', 'HSTS_INCLUDE_SUBDOMAINS', 'HSTS_PRELOAD') if key in values}


def recover(base, files_only=False):
    path = FOLDER / 'operation.json'
    if not path.exists() and not path.is_symlink():
        return
    operation = json.loads(protected(path, 32768, private=True))
    if (not isinstance(operation, dict) or operation.get('phase') not in ('prepared', 'verified')
            or not re.fullmatch(r'[0-9a-f]{32}', str(operation.get('id', '')))
            or type(operation.get('actor')) is not int or operation['actor'] < 0):
        raise ConfigError('Invalid management TLS recovery checkpoint.')
    current_hash = hashlib.sha256(protected(ENVIRONMENT).encode()).hexdigest()
    if current_hash not in (operation.get('beforeHash'), operation.get('afterHash')):
        raise ConfigError('The environment changed outside TLS maintenance. Review recovery manually.')
    before = protected(FOLDER / 'before-env', private=True)
    if hashlib.sha256(before.encode()).hexdigest() != operation['beforeHash']:
        raise ConfigError('The TLS recovery environment differs from its checkpoint.')
    if not files_only:
        run('systemctl', 'stop', UNIT)
    if operation['phase'] == 'verified':
        write(FOLDER / 'profile.json', json.dumps(operation['profile']))
    else:
        write(ENVIRONMENT, before, 0o640, ENVIRONMENT.stat().st_gid)
    if not files_only:
        run('systemctl', 'start', UNIT)
    audit('management_tls_recovered', operation, base)
    path.unlink()
    sync_directory(FOLDER)


def activate(sans, base, gid, candidate=None, requester=None):
    if (FOLDER / 'operation.json').exists():
        raise ConfigError('Recover the pending management TLS operation before continuing.')
    before = protected(ENVIRONMENT)
    values = deploy.manager_values(ENVIRONMENT, Path('/etc/systemd/system/proxypermit.service'))
    endpoint(values)
    previous = load_profile()
    if previous:
        expected = FOLDER / previous['generation']
        if values.get('MANAGER_CERT') != str(expected / 'cert.pem') or values.get('MANAGER_KEY') != str(expected / 'key.pem'):
            raise ConfigError('Management certificate paths differ from the owned profile; review the OS configuration.')
    elif values.get('MANAGER_SCHEME', 'http') != 'http' or values.get('GUNICORN_USE_TLS', '').lower() in ('1', 'true', 'yes'):
        raise ConfigError('Existing administrator-provided TLS will not be replaced automatically.')
    run('systemctl', 'is-active', '--quiet', UNIT)
    profile = candidate if candidate is not None else generate(sans, gid)
    if (not re.fullmatch(r'[0-9a-f]{32}', str(profile.get('generation', '')))
            or profile.get('sans') != sans):
        raise ConfigError('Invalid prepared management certificate.')
    target = FOLDER / profile['generation']
    check_pair(target / 'cert.pem', target / 'key.pem')
    if (certificate(target / 'cert.pem')['fingerprint'] != profile['fingerprint']
            or certificate(FOLDER / 'ca.pem')['fingerprint'] != profile['caFingerprint']
            or certificate(target / 'cert.pem')['expires'] <= time.time() + 86400):
        raise ConfigError('The prepared management certificate changed or expired.')
    run('openssl', 'verify', '-CAfile', str(FOLDER / 'ca.pem'), '-purpose', 'sslserver',
        '-verify_ip', '127.0.0.1', str(target / 'cert.pem'))
    updates = dict(MANAGER_SCHEME='https', GUNICORN_USE_TLS='true', SESSION_COOKIE_SECURE='true',
                   MANAGER_CERT=str(target / 'cert.pem'), MANAGER_KEY=str(target / 'key.pem'))
    if not previous:
        # HSTS is an explicit trust decision: a private CA must first be trusted
        # by the clients. Do not trap users behind an untrusted, cached origin.
        updates['ENABLE_HSTS'] = 'false'
    for path, content in assets(base).items():
        if path.exists() or path.is_symlink():
            if protected(path, 16384) != content:
                raise ConfigError('An existing management TLS service differs; review it before activation.')
    from scripts.deploy.logging_policy import write_asset
    for path, content in assets(base).items():
        write_asset(path, content)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', RENEW + '.timer')
    # Establish the successful boot guard before preparing a live transaction;
    # otherwise its first Requires= activation would roll back that transaction.
    run('systemctl', 'start', RECOVER)
    write(FOLDER / 'before-env', before)
    after = rewrite_environment(before, updates)
    operation = dict(id=profile['generation'], phase='prepared', profile=profile,
                     beforeHash=hashlib.sha256(before.encode()).hexdigest(),
                     afterHash=hashlib.sha256(after.encode()).hexdigest(),
                     actor=int(os.getenv('SUDO_UID', str(os.getuid()))))  # pylint: disable=no-member
    if requester:
        operation.update(actor=requester['uid'], requestedBy=requester['username'], requestId=requester['id'])
    write(FOLDER / 'operation.json', json.dumps(operation))
    audit('management_tls_requested', operation, base)
    try:
        run('systemctl', 'stop', UNIT)
        write(ENVIRONMENT, after, 0o640, gid)
        run('systemctl', 'start', UNIT)
        deadline = time.monotonic() + 25
        while not probe(dict(values, **updates), profile):
            if time.monotonic() >= deadline:
                raise ConfigError('The new management TLS listener failed verified readiness.')
            time.sleep(.3)
        operation['phase'] = 'verified'
        write(FOLDER / 'operation.json', json.dumps(operation))
        write(FOLDER / 'profile.json', json.dumps(profile))
        audit('management_tls_renewed' if previous else 'management_tls_enabled', operation, base)
        (FOLDER / 'operation.json').unlink()
        sync_directory(FOLDER)
    except (Exception, KeyboardInterrupt):
        recover(base)
        raise
    run('systemctl', 'start', RENEW + '.timer')
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enable', 'renew', 'status', 'recover'))
    parser.add_argument('--dns', action='append', default=[])
    parser.add_argument('--ip', action='append', default=[])
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--force', action='store_true', help='Renew before the normal 30-day threshold.')
    parser.add_argument('--files-only', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        deploy.require_linux_root('Management TLS maintenance')
        os.umask(0o077)
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', args.install_dir) or '..' in Path(args.install_dir).parts:
            raise ConfigError('Use an absolute installation path without spaces or parent traversal.')
        base = Path(args.install_dir) / 'current'
        for parent in (ENVIRONMENT.parent, *ENVIRONMENT.parent.parents, base.resolve(), *base.resolve().parents):
            deploy.require_trusted_path(parent, directory=True)
        protected(ENVIRONMENT)
        values = deploy.manager_values(ENVIRONMENT, Path('/etc/systemd/system/proxypermit.service'))
        endpoint(values)
        _, gid, _ = deploy.service_ownership()
        if args.action == 'enable':
            sans = names(args.dns, args.ip)
        else:
            sans = None
        if not args.apply or args.action == 'status':
            profile = load_profile() if FOLDER.exists() else None
            status = certificate(FOLDER / profile['generation'] / 'cert.pem') if profile else None
            print(json.dumps(dict(action=args.action, apply=False, names=sans, listener=endpoint(values),
                                  certificate=status, caFingerprint=profile.get('caFingerprint') if profile else None,
                                  caExpires=certificate(FOLDER / 'ca.pem')['expires'] if profile else None,
                                  renewalDue=bool(status and status['expires'] <= time.time() + RENEW_BEFORE * 86400)), indent=2))
            return 0
        directory(FOLDER, gid)
        if args.action == 'recover' and not (FOLDER / 'operation.json').exists() and not (FOLDER / 'operation.json').is_symlink():
            return 0
        with deploy.maintenance_lock(), file_lock(FOLDER / 'maintenance.lock'):
            if args.action == 'recover':
                recover(base, args.files_only)
                return 0
            if (FOLDER / 'operation.json').exists() or (FOLDER / 'operation.json').is_symlink():
                raise ConfigError('Recover the pending management TLS operation before continuing.')
            profile = load_profile()
            if args.action == 'renew':
                if not profile:
                    raise ConfigError('Enable managed management TLS before renewing it.')
                sans = profile['sans']
                if not args.force and certificate(FOLDER / profile['generation'] / 'cert.pem')['expires'] > time.time() + RENEW_BEFORE * 86400:
                    print('Management TLS certificate is not due for renewal.')
                    return 0
            elif profile:
                raise ConfigError('Management TLS is already configured. Use renew to retain its trust root.')
            result = activate(sans, base, gid)
            print(json.dumps(dict(certificate=result, caCertificate=str(FOLDER / 'ca.pem'),
                                  note='Trust the node CA explicitly. HSTS is not enabled automatically.'), indent=2))
        return 0
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Management TLS maintenance stopped. Review root recovery state and service logs; no proxy or VIP changes were requested.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
