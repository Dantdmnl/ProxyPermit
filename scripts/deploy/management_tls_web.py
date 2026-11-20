"""Fixed privileged HTTPS jobs; the web worker cannot select commands or paths."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import time

from app.core import management_https as control
from app.core.files import file_lock
from app.core.tinyproxy import ConfigError
from scripts.deploy import management_tls as tls

MARKER = '# Managed by ProxyPermit management HTTPS web control\n'
SUDOERS = Path('/etc/sudoers.d/proxypermit-management-tls-web')
RULE = 'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block proxypermit-management-tls-web.service\n'


def assets(base):
    return {Path('/etc/systemd/system') / control.UNIT: MARKER + (
        '[Unit]\nDescription=Apply reviewed ProxyPermit HTTPS web maintenance\nAfter=local-fs.target\n'
        '[Service]\nType=oneshot\nUser=root\nGroup=proxypermit\nUMask=0077\nLogNamespace=proxypermit\n'
        'RuntimeDirectory=proxypermit-management-tls-web\nRuntimeDirectoryMode=0750\nRuntimeDirectoryPreserve=yes\n'
        f'WorkingDirectory={base}/current\nExecStart={base}/current/.venv/bin/python '
        f'-m scripts.deploy.management_tls_web --install-dir {base}\nTimeoutStartSec=180\n'),
        SUDOERS: RULE, control.CAPABILITY: '{"version":1}\n'}


def read_request(uid):
    descriptor = os.open(control.REQUEST, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077
                or info.st_nlink != 1 or info.st_size > 16384 or not -5 <= time.time() - info.st_mtime <= 120):
            raise ConfigError('Invalid HTTPS maintenance request file.')
        value = json.load(source)
    if (not isinstance(value, dict) or set(value) != {'id', 'action', 'revision', 'plan', 'username', 'accountRevision', 'created'}
            or value['action'] not in ('prepare', 'activate', 'renew')
            or not isinstance(value['id'], str) or not re.fullmatch(r'[0-9a-f]{32}', value['id'])
            or not isinstance(value['revision'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['revision'])
            or not isinstance(value['plan'], str) or (value['plan'] and not re.fullmatch(r'[0-9a-f]{32}', value['plan']))
            or not isinstance(value['username'], str) or not 1 <= len(value['username']) <= 128 or not value['username'].isprintable()
            or not isinstance(value['accountRevision'], str) or not 1 <= len(value['accountRevision']) <= 256
            or type(value['created']) is not int or not -5 <= time.time() - value['created'] <= 120):
        raise ConfigError('Invalid HTTPS maintenance request.')
    return value


def verify_actor(request, uid):
    from app.core.accounts import account_role
    from dotenv import dotenv_values
    import io
    values = dotenv_values(stream=io.StringIO(tls.protected(tls.ENVIRONMENT)), interpolate=False)
    path = Path(values.get('AUTH_ACCOUNTS_FILE') or '/var/lib/proxypermit/accounts.json')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > 1024 * 1024):
            raise ConfigError('Untrusted account state.')
        accounts = json.load(source)
    account = accounts.get(request['username']) if isinstance(accounts, dict) else None
    if not account or account_role(account) != 'admin' or account.get('revision') != request['accountRevision']:
        raise ConfigError('The requesting administrator changed. Sign in again.')


def publish(request, phase, gid, **fields):
    tls.directory(control.RESULT_FOLDER, gid)
    tls.write(control.RESULT, json.dumps(dict(id=request['id'], action=request['action'],
              phase=phase, created=request['created'], **fields)), 0o640, gid)


def execute(request, base, uid, gid):
    with tls.deploy.maintenance_lock():
        tls.directory(tls.FOLDER, gid)
        with file_lock(tls.FOLDER / 'maintenance.lock'):
            verify_actor(request, uid)
            if (tls.FOLDER / 'operation.json').exists() or (tls.FOLDER / 'operation.json').is_symlink():
                raise ConfigError('Recover pending management TLS state before starting another job.')
            state = control.snapshot()
            if state['revision'] != request['revision']:
                raise ConfigError('HTTPS settings changed. Review the current node again.')
            requester = dict(uid=uid, username=request['username'], id=request['id'])
            if request['action'] == 'prepare':
                if not state['canEnable']:
                    raise ConfigError('Managed HTTPS cannot be enabled on this listener.')
                sans = tls.names([name[4:] for name in state['names'] if name.startswith('DNS:')],
                                 [name[3:] for name in state['names'] if name.startswith('IP:')])
                profile = tls.generate(sans, gid)
                plan = dict(id=profile['generation'], profile=profile, revision=state['revision'],
                            username=request['username'], expires=int(time.time()) + 600)
                tls.write(tls.FOLDER / 'web-plan.json', json.dumps(plan))
                tls.audit('management_tls_prepared', dict(id=plan['id'], actor=uid,
                          requestedBy=request['username'], requestId=request['id']), base)
                return dict(phase='prepared', plan=plan['id'], certificate=profile, expires=plan['expires'])
            if request['action'] == 'activate':
                plan = json.loads(tls.protected(tls.FOLDER / 'web-plan.json', 32768, private=True))
                if (not state['canEnable'] or plan.get('id') != request['plan']
                        or plan.get('revision') != state['revision'] or plan.get('username') != request['username']
                        or type(plan.get('expires')) is not int or not time.time() < plan['expires'] <= time.time() + 600):
                    raise ConfigError('The prepared HTTPS plan expired or changed. Prepare a new certificate.')
                profile = tls.activate(plan['profile']['sans'], base, gid, candidate=plan['profile'], requester=requester)
                (tls.FOLDER / 'web-plan.json').unlink()
            else:
                if not state['canRenew']:
                    raise ConfigError('Enable managed HTTPS before renewing its certificate.')
                profile = tls.load_profile()
                profile = tls.activate(profile['sans'], base, gid, requester=requester)
            return dict(phase='complete', certificate=profile)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    args = parser.parse_args(argv)
    request = None
    gid = 0
    try:
        tls.deploy.require_linux_root('HTTPS web maintenance')
        import pwd  # pylint: disable=import-error
        os.umask(0o077)
        base = Path(args.install_dir) / 'current'
        for path in (base.resolve(), *base.resolve().parents, control.REQUEST.parent.parent, tls.ENVIRONMENT.parent):
            tls.deploy.require_trusted_path(path, directory=True)
        user = pwd.getpwnam('proxypermit')
        gid = user.pw_gid
        request = read_request(user.pw_uid)
        publish(request, 'running', gid)
        # Consume the fixed filename only after durable running status. No
        # application-controlled lock pathname is ever opened as root.
        control.REQUEST.unlink()
        outcome = execute(request, base, user.pw_uid, gid)
        publish(request, outcome.pop('phase'), gid, **outcome)
        return 0
    except (OSError, ValueError, KeyError, TypeError, tls.subprocess.SubprocessError):
        if request:
            publish(request, 'failed', gid, error='HTTPS maintenance failed. Review the node settings and system log.')
        print('HTTPS web maintenance stopped; review the private service log and root TLS recovery state.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
