"""Deterministic native cluster configuration from an OS-approved two-node plan."""
import re
from xml.etree import ElementTree as ET

from .cluster_setup import plan_hash, validate_plan


def name(plan):
    value = validate_plan(plan)
    return 'proxypermit-' + plan_hash(value)[:12]


def corosync_config(plan):
    plan = validate_plan(plan)
    members = ''.join(f'''    node {{
        ring0_addr: {node['address']}
        name: {node['name']}
        nodeid: {number}
        quorum_votes: 1
    }}
''' for number, node in enumerate(plan['nodes'], 1))
    return f'''# Managed by ProxyPermit protected cluster
totem {{
    version: 2
    cluster_name: {name(plan)}
    transport: knet
    crypto_cipher: aes256
    crypto_hash: sha256
}}
nodelist {{
{members}}}
quorum {{
    provider: corosync_votequorum
    two_node: 0
    expected_votes: 3
    wait_for_all: 1
    device {{
        model: net
        votes: 1
        net {{
            host: {plan['witness']}
            algorithm: ffsplit
            tls: required
            tie_breaker: lowest
        }}
    }}
}}
logging {{
    to_syslog: yes
    timestamp: on
}}
'''


def firewall_rules(plan, role, interface):
    plan = validate_plan(plan)
    if role not in ('a', 'b', 'witness') or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        raise ValueError('Use an explicit cluster role and network interface.')
    def rule(direction, source, destination, port, protocol, label):
        return ['allow', direction, 'on', interface, 'from', source, 'to', destination,
                'port', str(port), 'proto', protocol, 'comment', 'ProxyPermit cluster ' + direction.upper() + ' - ' + label]
    if role == 'witness':
        return [rule('in', node['address'], plan['witness'], 5403, 'tcp', 'QNetd node ' + label)
                for label, node in zip(('A', 'B'), plan['nodes'])]
    index = 0 if role == 'a' else 1
    local, peer = plan['nodes'][index]['address'], plan['nodes'][1-index]['address']
    return [rule('in', peer, local, 5405, 'udp', 'Corosync peer'),
            rule('out', local, peer, 5405, 'udp', 'Corosync peer'),
            rule('out', local, plan['witness'], 5403, 'tcp', 'TLS quorum witness'),
            rule('out', local, plan['fenceAddress'], 8006, 'tcp', 'Verified Proxmox fencing')]


def initial_configuration(cib, plan, fence_host, pve_node):
    """Seed an empty CIB with a stopped VIP and fail-closed policy; no secrets."""
    plan = validate_plan(plan)
    if (plan['platform'] != 'proxmox' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', fence_host)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,62}', pve_node)):
        raise ValueError('The initial native workflow supports an explicit Proxmox endpoint.')
    configuration = cib.find('configuration')
    if configuration is None:
        raise ValueError('The live CIB has no configuration.')
    resources = configuration.find('resources')
    if resources is None or list(resources):
        raise ValueError('Initial setup must not replace existing cluster resources.')
    settings = configuration.find('crm_config')
    constraints = configuration.find('constraints')
    if settings is None or constraints is None or list(constraints):
        raise ValueError('Existing constraints require explicit cluster migration.')
    values = {pair.get('name'): pair.get('value') for pair in settings.iter('nvpair')}
    # Pacemaker can record a timestamp here after a resource cleanup.
    if (any(key not in ('cluster-infrastructure', 'cluster-name', 'dc-version', 'have-watchdog', 'last-lrm-refresh') for key in values)
            or ('last-lrm-refresh' in values and not re.fullmatch(r'[0-9]{1,20}', values['last-lrm-refresh'] or ''))
            or values.get('have-watchdog', 'false') != 'false'
            or values.get('cluster-name', name(plan)) != name(plan)):
        raise ValueError('Existing administrator cluster policy must not be silently replaced.')
    properties = ET.SubElement(settings, 'cluster_property_set', id='proxypermit-bootstrap-options')
    for key, value in {'stonith-enabled': 'true', 'startup-fencing': 'true', 'no-quorum-policy': 'stop',
                       'stonith-action': 'off', 'maintenance-mode': 'false'}.items():
        ET.SubElement(properties, 'nvpair', id='proxypermit-option-' + key, name=key, value=value)
    fencer = ET.SubElement(resources, 'primitive', {'id': 'proxypermit-fence', 'class': 'stonith', 'type': 'fence_proxypermit_pve'})
    attributes = ET.SubElement(fencer, 'instance_attributes', id='proxypermit-fence-options')
    hosts = plan['nodes']
    for key, value in {'ipaddr': fence_host, 'pve_node': pve_node, 'vmtype': 'qemu', 'ssl': 'true',
                       'pcmk_host_check': 'static-list', 'pcmk_host_list': ' '.join(node['name'] for node in hosts),
                       'pcmk_host_map': ';'.join(node['name'] + ':' + node['target'] for node in hosts),
                       'pcmk_off_timeout': '60s'}.items():
        ET.SubElement(attributes, 'nvpair', id='proxypermit-fence-' + key, name=key, value=value)
    operations = ET.SubElement(fencer, 'operations')
    ET.SubElement(operations, 'op', id='proxypermit-fence-monitor', name='monitor', interval='30s', timeout='10s')
    vip = ET.SubElement(resources, 'primitive', {'id': 'proxypermit-vip', 'class': 'ocf', 'provider': 'proxypermit', 'type': 'vip'})
    metadata = ET.SubElement(vip, 'meta_attributes', id='proxypermit-vip-options')
    for key, value in {'requires': 'fencing', 'migration-threshold': '1', 'resource-stickiness': '1000', 'target-role': 'Stopped'}.items():
        ET.SubElement(metadata, 'nvpair', id='proxypermit-vip-' + key, name=key, value=value)
    operations = ET.SubElement(vip, 'operations')
    for operation, interval, timeout in [('monitor', '5s', '10s'), ('start', '0s', '90s'), ('stop', '0s', '60s')]:
        ET.SubElement(operations, 'op', id='proxypermit-vip-' + operation, name=operation, interval=interval, timeout=timeout)
    ET.SubElement(constraints, 'rsc_location', id='proxypermit-prefer-a', rsc='proxypermit-vip', node=hosts[0]['name'], score='100')
    return cib
