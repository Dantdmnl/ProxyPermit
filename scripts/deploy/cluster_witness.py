"""Prepare a dedicated qnetd witness with mandatory TLS/client certificates.

No proxy node or VIP is changed. Firewall management access must already work;
this module never enables UFW or opens a remote cluster-administration service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from app.core import cluster_setup, cluster_topology
from app.core.tinyproxy import atomic_write
from scripts.deploy import cluster_bootstrap as bootstrap, install, logging_policy

STATE = bootstrap.FOLDER / 'witness.json'
CA = Path('/etc/corosync/qnetd/nssdb/qnetd-cacert.crt')
MARKER = '# Managed by ProxyPermit quorum witness\n'
DROPIN = Path('/etc/systemd/system/corosync-qnetd.service.d/30-proxypermit.conf')


def assets(plan):
    plan = cluster_setup.validate_plan(plan)
    namespace = Path('/etc/systemd/journald@proxypermit.conf.d/20-proxypermit.conf')
    return {
        DROPIN: MARKER + '[Service]\nExecStart=\n'
            f"ExecStart=/usr/bin/corosync-qnetd -f -4 -l {plan['witness']} -p 5403 -s req -c on -m 8\n"
            'StandardError=journal\nLogNamespace=proxypermit\nNoNewPrivileges=yes\nProtectHome=yes\nProtectSystem=strict\n'
            'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n',
        namespace: logging_policy.assets(Path('/opt/proxypermit/current'), {})[namespace],
        Path('/etc/systemd/system/proxypermit-witness-retention.service'): MARKER +
            '[Unit]\nDescription=Expire only the private witness journal\n[Service]\nType=oneshot\n'
            'LogNamespace=proxypermit\n'
            'ExecStart=/usr/bin/journalctl --namespace=proxypermit --rotate --vacuum-time=30d --vacuum-size=100M\n'
            'PrivateTmp=yes\nProtectHome=yes\nTimeoutStartSec=60\n',
        Path('/etc/systemd/system/proxypermit-witness-retention.timer'): MARKER +
            '[Unit]\nDescription=Hourly private witness journal retention\n[Timer]\n'
            'OnCalendar=hourly\nPersistent=yes\nRandomizedDelaySec=60\n[Install]\nWantedBy=timers.target\n',
    }


def configure():
    install.require_linux_root('Witness preparation')
    with install.maintenance_lock():
        prepared = cluster_setup.protected(bootstrap.JOURNAL, private=True)
        proposed = bootstrap.plan('witness')
        if (not isinstance(prepared, dict) or prepared.get('phase') != 'ready'
                or prepared.get('planHash') != proposed['planHash'] or prepared.get('role') != 'witness'):
            raise ValueError('Complete the matching witness package preparation first.')
        plan = cluster_setup.validate_plan(cluster_setup.protected(cluster_setup.PLAN, private=True))
        if bootstrap.run('/usr/bin/systemctl', 'show', 'corosync-qnetd.service', '--property=User', '--value').stdout.strip() != 'coroqnetd':
            raise ValueError('The witness must use the distribution unprivileged service account.')
        if 'Status: active' not in bootstrap.run('/usr/sbin/ufw', 'status').stdout:
            raise ValueError('Prepare active UFW and pinned management access before starting the witness.')
        # The CA is created by the OS package, not silently regenerated here.
        info = CA.lstat()
        if not CA.is_file() or CA.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1:
            raise ValueError('The existing OS qnetd CA needs review.')
        bootstrap.run('/usr/bin/openssl', 'x509', '-in', str(CA), '-noout', '-checkend', '86400')
        ca_hash = hashlib.sha256(CA.read_bytes()).hexdigest()
        route = json.loads(bootstrap.run('/usr/sbin/ip', '-j', '-4', 'route', 'get', plan['nodes'][0]['address']).stdout)
        if len(route) != 1 or route[0].get('prefsrc') != plan['witness']:
            raise ValueError('The witness route does not match the approved source address.')
        rules = cluster_topology.firewall_rules(plan, 'witness', route[0]['dev'])
        desired = assets(plan)
        state = None
        if STATE.exists() or STATE.is_symlink():
            state = cluster_setup.protected(STATE, private=True)
            if (not isinstance(state, dict) or state.get('version') != 1
                    or state.get('phase') not in ('prepared', 'listening')
                    or not isinstance(state.get('beforeRules'), list)
                    or any(not isinstance(rule, str) for rule in state['beforeRules'])
                    or state.get('planHash') != proposed['planHash'] or state.get('caHash') != ca_hash):
                raise ValueError('The witness plan or CA changed; review recovery instead of replacing trust.')
        else:
            if bootstrap.run('/usr/bin/systemctl', 'is-active', '--quiet', 'corosync-qnetd.service', check=False).returncode == 0:
                raise ValueError('An existing active witness must not be adopted automatically.')
        for path, content in desired.items():
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_mode & 0o022 or path.read_text() != content:
                    raise ValueError('An existing witness asset differs; preserve it for OS review.')
        if state is None:
            state = dict(version=1, phase='prepared', planHash=proposed['planHash'], caHash=ca_hash,
                         beforeRules=sorted(install.ufw_added_rules()), created=int(time.time()))
            atomic_write(STATE, json.dumps(state), 0o600)
        install.apply_ha_firewall(rules)
        after = install.ufw_added_rules()
        before = set(state['beforeRules'])
        if before - after:
            raise ValueError('An earlier firewall rule changed; review witness preparation.')
        state['ownedRules'] = sorted(after - before)
        atomic_write(STATE, json.dumps(state), 0o600)
        for path, content in desired.items():
            logging_policy.write_asset(path, content)
        bootstrap.run('/usr/bin/systemctl', 'daemon-reload')
        bootstrap.run('/usr/bin/systemctl', 'enable', '--now', 'proxypermit-witness-retention.timer')
        bootstrap.run('/usr/bin/systemctl', 'enable', '--now', 'corosync-qnetd.service')
        bootstrap.run('/usr/bin/systemctl', 'is-active', '--quiet', 'corosync-qnetd.service')
        with socket.create_connection((plan['witness'], 5403), timeout=2):
            pass  # Listener check only; client authentication is verified separately.
        state['phase'] = 'listening'
        atomic_write(STATE, json.dumps(state), 0o600)
        return dict(state='listening', cluster=cluster_topology.name(plan), tlsRequired=True, clientCertificateRequired=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            value = configure()
        else:
            value = dict(state='plan', files=[str(path) for path in assets(cluster_setup.protected(cluster_setup.PLAN, private=True))])
        print(json.dumps(value, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Witness preparation stopped. Retained state and existing CA require OS review; no trust was regenerated.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
