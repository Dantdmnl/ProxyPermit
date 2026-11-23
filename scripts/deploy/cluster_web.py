"""Apply a reviewed local HA plan or install its fixed OS dependencies."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from app.core import cluster_setup as control
from scripts.deploy import cluster_bootstrap, cluster_setup, install, management_tls
from scripts.deploy.management_tls_web import verify_actor

MARKER = control.WEB_MARKER
SUDOERS = Path('/etc/sudoers.d/proxypermit-cluster-web')
RULE = 'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block proxypermit-cluster-web.service\n'


def assets(base):
    return {Path('/etc/systemd/system') / control.WEB_UNIT: MARKER + (
        '[Unit]\nDescription=Prepare reviewed local ProxyPermit HA settings\n'
        '[Service]\nType=oneshot\nUser=root\nGroup=proxypermit\nUMask=0077\nLogNamespace=proxypermit\n'
        'RuntimeDirectory=proxypermit-cluster-web\nRuntimeDirectoryMode=0750\nRuntimeDirectoryPreserve=yes\n'
        'Environment=PYTHONNOUSERSITE=1\n'
        f'WorkingDirectory={base}/current\nExecStart={base}/current/.venv/bin/python '
        f'-m scripts.deploy.cluster_web --install-dir {base}\nTimeoutStartSec={control.JOB_TIMEOUT}\n'),
        SUDOERS: RULE}


def read_request(uid):
    fd = os.open(control.WEB_REQUEST, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077
                or info.st_nlink != 1 or info.st_size > 4096
                or not -5 <= time.time() - info.st_mtime <= control.REQUEST_AGE):
            raise ValueError('Invalid local cluster request file.')
        value = json.loads(source.read(4097))
    if (not isinstance(value, dict) or set(value) != {'id', 'action', 'plan', 'expected', 'username', 'accountRevision', 'created'}
            or value['action'] not in control.WEB_ACTIONS or not control.plan_revision(value['expected'])
            or not isinstance(value['id'], str) or not re.fullmatch(r'[0-9a-f]{32}', value['id'])
            or not isinstance(value['username'], str) or not 1 <= len(value['username']) <= 128
            or not value['username'].isprintable() or not isinstance(value['accountRevision'], str)
            or not 1 <= len(value['accountRevision']) <= 256 or type(value['created']) is not int
            or not -5 <= time.time() - value['created'] <= control.REQUEST_AGE):
        raise ValueError('Invalid local cluster request.')
    if value['action'] == 'stage':
        value['plan'] = control.validate_plan(value['plan'])
        if value['plan']['platform'] != 'proxmox':
            raise ValueError('This local workflow uses the native Proxmox adapter.')
    elif value['plan'] is not None or not value['expected']:
        raise ValueError('Dependency preparation requires the reviewed saved plan.')
    return value


def execute(job, uid, base):
    verify_actor(job, uid)
    if job['action'] == 'stage':
        cluster_setup.stage_plan(job['plan'], 'node', expected=job['expected'])
        result = control.plan_hash(job['plan'])
    else:
        result = cluster_bootstrap.prepare('node', expected_hash=job['expected'])['planHash']
    cluster_bootstrap.run('/usr/sbin/runuser', '-u', 'proxypermit', '--', str(base / '.venv/bin/python'),
        '-m', 'scripts.tools.record_maintenance', 'cluster_web_plan_saved' if job['action'] == 'stage' else 'cluster_web_packages_prepared',
        job['id'], str(uid), '--requested-by', job['username'], '--request-id', job['id'], cwd=base)
    return result


def publish(job, phase, gid, **fields):
    management_tls.directory(control.WEB_RESULT.parent, gid)
    management_tls.write(control.WEB_RESULT, json.dumps(dict(id=job['id'], action=job['action'],
        phase=phase, created=job['created'], **fields)), 0o640, gid)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    args = parser.parse_args(argv)
    job = None
    gid = 0
    try:
        install.require_linux_root('Local cluster web preparation')
        import pwd  # pylint: disable=import-error
        user = pwd.getpwnam('proxypermit')
        gid = user.pw_gid
        base = (Path(args.install_dir) / 'current').resolve()
        for folder in (base, *base.parents, control.WEB_REQUEST.parent.parent):
            install.require_trusted_path(folder, directory=True)
        job = read_request(user.pw_uid)
        publish(job, 'running', gid)
        control.WEB_REQUEST.unlink()
        result = execute(job, user.pw_uid, base)
        publish(job, 'complete', gid, planHash=result)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.SubprocessError):
        if job:
            publish(job, 'failed', gid)
        print('Local cluster preparation stopped. Review the staged plan, retained OS preparation state and this private service log.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
