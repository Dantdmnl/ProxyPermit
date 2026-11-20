"""Experimental root-only phases for a dedicated, OS-prepared Proxmox pair.

A trusted operator coordinates both nodes over authenticated SSH. This module
is deliberately not callable through a web sudo rule. A failed phase retains
the reservation and stopped independent Keepalived until explicit recovery.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from xml.etree import ElementTree as ET

from app.core import cluster, cluster_setup, cluster_topology
from app.core.files import sync_directory
from app.core.ha import ha_topology
from app.core.tinyproxy import atomic_write
from scripts.deploy import cluster_bootstrap as bootstrap, cluster_maintenance, cluster_status, ha_resource, install, logging_policy

STATE = Path('/etc/proxypermit/cluster-bootstrap.json')
MARKER = '# Managed by ProxyPermit native cluster\n'
AGENT = Path('/usr/lib/ocf/resource.d/proxypermit/vip')
KEY = Path('/etc/corosync/authkey')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_bytes(path, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # pylint: disable=no-member
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > maximum:
            raise ValueError('Native cluster input must be a bounded root-only file.')
        value = source.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError('Native cluster input is too large.')
        return value


def write_key(path, value):
    """Durably publish binary key material without text encoding or inherited ACLs."""
    descriptor, temporary = tempfile.mkstemp(prefix='.proxypermit-key-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o400)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def context(operation):
    from dotenv import dotenv_values
    from scripts.deploy.management_tls import ENVIRONMENT, protected
    if not re.fullmatch(r'[0-9a-f]{32}', operation):
        raise ValueError('Use one explicit setup operation across both nodes.')
    plan = cluster_setup.validate_plan(cluster_setup.protected(cluster_setup.PLAN, private=True))
    if plan['platform'] != 'proxmox':
        raise ValueError('The initial native workflow currently supports the tested Proxmox adapter.')
    values = dotenv_values(stream=io.StringIO(protected(ENVIRONMENT)), interpolate=False)
    role = values.get('PROXYPERMIT_HA_NODE')
    if values.get('PROXYPERMIT_MANAGED_HA') != 'true' or role not in ('a', 'b'):
        raise ValueError('Migrate a managed, paired two-node installation only.')
    topology = ha_topology(role, values['PROXYPERMIT_HA_INTERFACE'], values['NODE0_ADDRESS'], values['NODE1_ADDRESS'],
        values['VIP_ADDRESS'] + '/' + values['HA_VIP_PREFIX'], values['HA_CLIENT_CIDR'], int(values['HA_VRID']))
    index = 0 if role == 'a' else 1
    if ([node['address'] for node in plan['nodes']] != [topology['nodeA'], topology['nodeB']]
            or socket.gethostname().split('.')[0] != plan['nodes'][index]['name']):
        raise ValueError('The native plan must match the installed node identities and addresses.')
    from scripts.deploy.management_tls import protected as text
    if text(Path('/etc/keepalived/keepalived.conf')) != install.keepalived_text(topology):
        raise ValueError('An administrator-modified Keepalived configuration needs an explicit migration plan.')
    stamp = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    folder = bootstrap.FOLDER / operation
    return plan, topology, stamp, folder


def save(state):
    atomic_write(STATE, json.dumps(state), 0o600)


def load(operation):
    plan, topology, stamp, folder = context(operation)
    state = cluster_setup.protected(STATE, private=True)
    host_hash = hashlib.sha256(json.dumps(topology, sort_keys=True).encode()).hexdigest()
    if (not isinstance(state, dict) or state.get('operation') != operation or state.get('planHash') != stamp
            or state.get('hostHash') != host_hash
            or state.get('phase') not in ('reserving', 'reserved', 'transport', 'running', 'configured', 'activating', 'active')):
        raise ValueError('The retained native operation differs; review recovery before proceeding.')
    if state['phase'] != 'reserving':
        expected = dict(version=1, interface=topology['interface'], vip=topology['vip'])
        if cluster_setup.protected(cluster.PROFILE, private=True) != expected:
            raise ValueError('The retained VIP reservation changed; stop for OS review.')
        ha_resource.validate_service()
    return plan, topology, folder, state


def audit(event, operation):
    base = Path('/opt/proxypermit/current')
    bootstrap.run('/usr/sbin/runuser', '-u', 'proxypermit', '--', str(base / '.venv/bin/python'),
        '-m', 'scripts.tools.record_maintenance', event, operation, os.getenv('SUDO_UID', '0'), cwd=base)


def require_prepared(stamp):
    prepared = cluster_setup.protected(bootstrap.JOURNAL, private=True)
    if not isinstance(prepared, dict) or prepared.get('phase') != 'ready' or prepared.get('role') != 'node' or prepared.get('planHash') != stamp:
        raise ValueError('Finish the matching dependency preparation first.')
    bootstrap.plan('node')  # Recheck local address and genuine OS time status.
    for command in ('corosync', 'corosync-qdevice-tool', 'corosync-quorumtool', 'crm_mon', 'cibadmin', 'crm_node'):
        if shutil.which(command, path=bootstrap.ENVIRONMENT['PATH']) is None:
            raise ValueError('A required native cluster executable is missing: ' + command)


def verify_power_receipt(folder, stamp):
    receipt = cluster_setup.protected(folder / 'fencing-tested.json', private=True)
    credential = cluster_setup.protected(Path('/etc/proxypermit/proxmox-fencing.json'), private=True)
    expected = dict(profileHash=digest('/etc/proxypermit/proxmox-fencing.json'),
        caHash=digest('/etc/proxypermit/proxmox-fencing-ca.pem'), agentHash=digest('/usr/sbin/fence_proxypermit_pve'),
        planHash=stamp, target=credential['target'])
    if (not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items())
            or receipt.get('offConfirmed') is not True or receipt.get('recoveryConfirmed') is not True
            or type(receipt.get('checked')) is not int or not -5 <= time.time() - receipt['checked'] <= 86400):
        raise ValueError('An OS-verified off/recovery receipt for the current credential and agent is required.')


def reserve(operation):
    plan, topology, stamp, folder = context(operation)
    require_prepared(stamp)
    install.require_trusted_path(bootstrap.FOLDER, directory=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    install.require_trusted_path(folder, directory=True)
    if folder.stat().st_mode & 0o077:
        raise ValueError('Native operation storage must be private.')
    if STATE.exists() or STATE.is_symlink():
        _, _, _, state = load(operation)
        if state['phase'] != 'reserving':
            return state
    else:
        if cluster.reserved():
            raise ValueError('An existing protected cluster must use its maintenance workflow.')
        for unit in ('pacemaker.service', 'corosync.service', 'corosync-qdevice.service'):
            if bootstrap.run('/usr/bin/systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0:
                raise ValueError('Do not adopt an existing active cluster.')
        verify_power_receipt(folder, stamp)
        local_profile = dict(version=1, interface=topology['interface'], vip=topology['vip'])
        if ha_resource.owns_vip({**local_profile, 'address': topology['vip'].split('/')[0]}):
            peer = cluster_setup.protected(folder / 'peer-reserved.json', private=True)
            if (not isinstance(peer, dict) or peer.get('operation') != operation or peer.get('planHash') != stamp
                    or peer.get('phase') != 'reserved' or peer.get('vipPresent') is not False
                    or peer.get('name') != plan['nodes'][1 if topology['node'] == 'a' else 0]['name']):
                raise ValueError('Drain and verify the standby node before stopping the current VIP owner.')
        state = dict(version=1, operation=operation, planHash=stamp, phase='reserving',
                     hostHash=hashlib.sha256(json.dumps(topology, sort_keys=True).encode()).hexdigest(),
                     name=socket.gethostname().split('.')[0], vipPresent=False)
        # Keep the old, package-supplied Corosync configuration for explicit recovery.
        if bootstrap.CONFIG.exists():
            from scripts.deploy.management_tls import protected
            original = protected(bootstrap.CONFIG)
            conffiles = bootstrap.run('/usr/bin/dpkg-query', '-W', '-f=${Conffiles}', 'corosync').stdout
            expected_md5 = next((row.split()[1] for row in conffiles.splitlines()
                                 if row.split() and row.split()[0] == str(bootstrap.CONFIG)), None)
            if hashlib.md5(original.encode(), usedforsecurity=False).hexdigest() != expected_md5:
                raise ValueError('The existing Corosync configuration differs from the OS package default.')
            atomic_write(folder / 'before-corosync.conf', original, 0o600)
        state['keepalivedEnabled'] = bootstrap.run('/usr/bin/systemctl', 'is-enabled', 'keepalived.service', check=False).stdout.strip()
        save(state)
        audit('cluster_setup_reserved', operation)
    profile = dict(version=1, interface=topology['interface'], vip=topology['vip'])
    atomic_write(cluster.PROFILE, json.dumps(profile), 0o600)
    dropin = MARKER + '[Service]\nKillMode=control-group\nSendSIGKILL=yes\nRestart=no\nTimeoutStopSec=10s\n'
    if cluster.SERVICE_RESERVATION.exists() or cluster.SERVICE_RESERVATION.is_symlink():
        if cluster.SERVICE_RESERVATION.is_symlink() or cluster.SERVICE_RESERVATION.read_text() != dropin:
            raise ValueError('The Keepalived reservation changed; leave the operation reserved.')
    logging_policy.write_asset(cluster.SERVICE_RESERVATION, dropin)
    bootstrap.run('/usr/bin/systemctl', 'daemon-reload')
    bootstrap.run('/usr/bin/systemctl', 'disable', 'keepalived.service')
    bootstrap.run('/usr/bin/systemctl', 'stop', 'keepalived.service', timeout=25)
    if ha_resource.owns_vip({**profile, 'address': topology['vip'].split('/')[0]}):
        raise ValueError('The VIP was not safely removed; do not activate a new owner.')
    ha_resource.validate_service()
    state['phase'] = 'reserved'
    save(state)
    return state


def transport(operation):
    plan, topology, folder, state = load(operation)
    if state['phase'] not in ('reserved', 'transport'):
        raise ValueError('Reserve the node before configuring cluster transport.')
    key = private_bytes(folder / 'authkey', 1024)
    if len(key) != 1024:
        raise ValueError('Use a fresh 1024-byte shared Corosync key for this operation.')
    ready = cluster_setup.protected(folder / 'qdevice-ready.json', private=True)
    if not isinstance(ready, dict) or ready.get('cluster') != cluster_topology.name(plan) or ready.get('planHash') != state['planHash']:
        raise ValueError('Complete the matching native qdevice certificate enrollment first.')
    if KEY.exists() or KEY.is_symlink():
        if private_bytes(KEY, 1024) != key:
            raise ValueError('An existing Corosync key differs; preserve it for explicit recovery.')
    configuration = cluster_topology.corosync_config(plan)
    if bootstrap.CONFIG.exists() or bootstrap.CONFIG.is_symlink():
        from scripts.deploy.management_tls import protected
        current_config = protected(bootstrap.CONFIG)
        original = folder / 'before-corosync.conf'
        if current_config != configuration and (not original.exists() or private_bytes(original).decode() != current_config):
            raise ValueError('The reserved Corosync configuration changed; preserve administrator changes.')
    before = state.setdefault('beforeRules', sorted(install.ufw_added_rules()))
    save(state)
    install.apply_ha_firewall(cluster_topology.firewall_rules(plan, topology['node'], topology['interface']))
    current = install.ufw_added_rules()
    if set(before) - current:
        raise ValueError('Existing firewall rules changed; review the reserved operation.')
    state['ownedRules'] = sorted(current - set(before))
    save(state)
    write_key(KEY, key)
    atomic_write(bootstrap.CONFIG, configuration, 0o640)
    content = Path(ha_resource.__file__).read_text()
    if AGENT.exists() or AGENT.is_symlink():
        if AGENT.is_symlink() or AGENT.read_text() != content:
            raise ValueError('An existing OCF agent differs; preserve it for review.')
    logging_policy.write_asset(AGENT, content)
    AGENT.chmod(0o755)
    for unit in ('corosync', 'corosync-qdevice', 'pacemaker'):
        target = Path('/etc/systemd/system') / (unit + '.service.d/30-proxypermit-logging.conf')
        expected = MARKER + '[Service]\nLogNamespace=proxypermit\n'
        if target.exists() or target.is_symlink():
            from scripts.deploy.management_tls import protected
            if protected(target) != expected:
                raise ValueError('An existing cluster logging policy differs.')
        logging_policy.write_asset(target, expected)
    bootstrap.run('/usr/bin/systemctl', 'daemon-reload')
    state['phase'] = 'transport'
    state['configHash'] = digest(bootstrap.CONFIG)
    state['keyHash'] = digest(KEY)
    save(state)
    return state


def start(operation):
    _, _, _, state = load(operation)
    if state['phase'] not in ('transport', 'running') or state.get('configHash') != digest(bootstrap.CONFIG) or state.get('keyHash') != digest(KEY):
        raise ValueError('Cluster transport differs from the reserved operation.')
    bootstrap.plan('node')
    ha_resource.validate_service()
    bootstrap.run('/usr/sbin/corosync', '-t')
    bootstrap.run('/usr/bin/systemctl', 'start', 'corosync.service')
    bootstrap.run('/usr/bin/systemctl', 'start', 'corosync-qdevice.service')
    state['phase'] = 'running';save(state)
    audit('cluster_setup_transport_started', operation)
    return state


def configuration_matches(cib, plan, host, pve_node, target_role='Stopped'):
    # Rebuild only the exact owned bootstrap sections; retain CIB generations
    # and all runtime state for version-checked replacement.
    candidate = ET.fromstring(ET.tostring(cib))
    cfg = candidate.find('configuration')
    if cfg is None:
        return False
    resources, constraints, settings = (cfg.find(key) for key in ('resources', 'constraints', 'crm_config'))
    if resources is None or constraints is None or settings is None:
        return False
    if {item.get('id') for item in resources} != {'proxypermit-fence', 'proxypermit-vip'}:
        return False
    if {item.get('id') for item in constraints} != {'proxypermit-prefer-a'}:
        return False
    resources.clear();constraints.clear()
    own = settings.find("cluster_property_set[@id='proxypermit-bootstrap-options']")
    if own is None:
        return False
    settings.remove(own)
    cluster_topology.initial_configuration(candidate, plan, host, pve_node)
    candidate.find("configuration/resources/primitive[@id='proxypermit-vip']/meta_attributes/nvpair[@name='target-role']").set('value', target_role)
    for xpath in ('configuration/resources', 'configuration/constraints', "configuration/crm_config/cluster_property_set[@id='proxypermit-bootstrap-options']"):
        if ET.canonicalize(ET.tostring(cib.find(xpath), encoding='unicode'), strip_text=True) != ET.canonicalize(ET.tostring(candidate.find(xpath), encoding='unicode'), strip_text=True):
            return False
    return True


def pacemaker(operation):
    _, _, _, state = load(operation)
    if state['phase'] != 'running':
        raise ValueError('Start and verify the encrypted transport first.')
    device = bootstrap.run('/usr/sbin/corosync-qdevice-tool', '-s', '-v').stdout
    quorum = bootstrap.run('/usr/sbin/corosync-quorumtool', '-s').stdout
    if (not re.search(r'^State:\s+Connected\s*$', device, re.M)
            or not re.search(r'^TLS active:\s+Yes \(client certificate sent\)\s*$', device, re.M)
            or not re.search(r'^Expected votes:\s+3\s*$', device, re.M)
            or not re.search(r'^Nodes:\s+2\s*$', quorum, re.M)
            or not re.search(r'^Quorate:\s+Yes\s*$', quorum, re.M)):
        raise ValueError('Both nodes and a certificate-authenticated quorum device must be ready.')
    bootstrap.run('/usr/bin/systemctl', 'start', 'pacemaker.service')
    return state


def seed(operation):
    plan, _, _, state = load(operation)
    if state['phase'] not in ('running', 'configured'):
        raise ValueError('Start and verify both native transports first.')
    # Starting Pacemaker is coordinated on BOTH nodes before this operation.
    monitor = cluster_maintenance.xml(bootstrap.run('crm_mon', '--one-shot', '--as-xml').stdout)
    nodes = monitor.findall('nodes/node')
    if {node.get('name') for node in nodes if node.get('online') == 'true'} != {node['name'] for node in plan['nodes']}:
        raise ValueError('Both approved Pacemaker nodes must be online before adding fencing.')
    credential = cluster_setup.protected(Path('/etc/proxypermit/proxmox-fencing.json'), private=True)
    for _ in range(3):
        cib = cluster_maintenance.query()
        if configuration_matches(cib, plan, credential['host'], credential['pveNode']):
            break
        cluster_topology.initial_configuration(cib, plan, credential['host'], credential['pveNode'])
        if cluster_maintenance.replace(cib):
            break
    else:
        raise ValueError('The CIB changed concurrently; keep the VIP stopped and review the operation.')
    if not configuration_matches(cluster_maintenance.query(), plan, credential['host'], credential['pveNode']):
        raise ValueError('The initial configuration was not confirmed; keep the VIP stopped.')
    state['phase'] = 'configured';save(state)
    audit('cluster_setup_configuration_seeded', operation)
    return state


def activate(operation):
    plan, _, folder, state = load(operation)
    if state['phase'] not in ('configured', 'activating'):
        raise ValueError('Configure the stopped resource before explicit activation.')
    bootstrap.plan('node')
    credential = cluster_setup.protected(Path('/etc/proxypermit/proxmox-fencing.json'), private=True)
    cib = cluster_maintenance.query()
    if state['phase'] == 'configured':
        verify_power_receipt(folder, state['planHash'])
        if not configuration_matches(cib, plan, credential['host'], credential['pveNode']):
            raise ValueError('The reviewed cluster configuration changed before activation.')
        evidence = cluster_status.evidence()
        if evidence['owners'] != 0 or not all(evidence[key] for key in ('quorate', 'witnessConnected', 'fencingConfigured', 'fencingReady', 'policySafe')):
            raise ValueError('Confirm quorum, mutual TLS and ready fencing while the VIP remains stopped.')
        target = cib.find("configuration/resources/primitive[@id='proxypermit-vip']/meta_attributes/nvpair[@name='target-role']")
        target.set('value', 'Started')
        state['phase'] = 'activating';save(state)
        audit('cluster_setup_activation_requested', operation)
        if not cluster_maintenance.replace(cib):
            raise ValueError('Activation reply uncertain; re-read cluster status before any retry.')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if cluster_status.evidence()['state'] == 'protected':
            return finalize(operation)
        time.sleep(1)
    raise ValueError('Activation remains unverified; retain the checkpoint and inspect native status.')


def finalize(operation):
    plan, _, _, state = load(operation)
    if state['phase'] not in ('configured', 'activating', 'active'):
        raise ValueError('Finalize only a configured and verified operation.')
    credential = cluster_setup.protected(Path('/etc/proxypermit/proxmox-fencing.json'), private=True)
    if (not configuration_matches(cluster_maintenance.query(), plan, credential['host'], credential['pveNode'], 'Started')
            or cluster_status.evidence()['state'] != 'protected'):
        raise ValueError('Confirm the exact active policy and exclusive healthy ownership first.')
    # Keepalived remains disabled at boot. Only Pacemaker starts its resource.
    bootstrap.run('/usr/bin/systemctl', 'enable', 'corosync.service', 'corosync-qdevice.service', 'pacemaker.service')
    previous = state['phase']
    state['phase'] = 'active'
    state['vipPresent'] = ha_resource.owns_vip(ha_resource.profile())
    save(state)
    if previous != 'active':
        audit('cluster_setup_activated', operation)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--phase', choices=('reserve', 'transport', 'start', 'pacemaker', 'seed', 'activate', 'finalize'), required=True)
    parser.add_argument('--experimental', action='store_true', help='Explicitly acknowledge the OS-prepared experimental workflow.')
    args = parser.parse_args(argv)
    try:
        install.require_linux_root('Native cluster setup')
        if not args.experimental:
            raise ValueError('Experimental native setup requires explicit operator acknowledgement.')
        with install.maintenance_lock():
            result = globals()[args.phase](args.operation)
        print(json.dumps({key: result[key] for key in ('operation', 'planHash', 'phase', 'name', 'vipPresent')}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print('Native cluster setup stopped. Keep the retained reservation and inspect OS recovery before retrying.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
