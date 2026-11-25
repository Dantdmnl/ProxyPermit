"""Reduce local cluster evidence to a small status without CIB credentials."""
import re
from xml.etree import ElementTree as ET

RESOURCE = 'proxypermit-vip'
FENCERS = frozenset(('fence_xvm', 'fence_pve', 'fence_proxypermit_pve', 'fence_vmware_rest', 'fence_ipmilan', 'fence_virsh'))
HTTPS_FENCERS = frozenset(('fence_pve', 'fence_proxypermit_pve', 'fence_vmware_rest'))


def xml(text):
    if (not isinstance(text, str) or len(text.encode('utf-8')) > 2 * 1024 * 1024
            or '<!DOCTYPE' in text or '<!ENTITY' in text):
        raise ValueError('Invalid cluster XML evidence.')
    return ET.fromstring(text)


def properties(parent):
    if parent is None:
        return {}
    pairs = list(parent.iter('nvpair'))
    if any(True for _ in parent.iter('rule')) or len({item.get('name') for item in pairs}) != len(pairs):
        return {}  # Ambiguous/rule-driven policy needs explicit operator review.
    return {item.get('name'): item.get('value') for item in pairs}


def enabled(value):
    return value in ('true', '1', 'yes', 'on')


def fencing_coverage(fencers, ready, members):
    covered, available = set(), set()
    targets = {}
    for item in fencers:
        values = properties(item.find('instance_attributes'))
        metadata = item.findall('meta_attributes')
        if (len(item.findall('instance_attributes')) != 1 or len(metadata) > 1
                or any(True for _ in item.iter('rule'))):
            return False, False
        meta = properties(metadata[0]) if metadata else {}
        if meta.get('target-role', '').lower() == 'stopped' or meta.get('is-managed') in ('false', '0', 'no', 'off'):
            continue
        hosts = set(values.get('pcmk_host_list', '').split())
        entries = [entry.strip().split(':', 1) for entry in values.get('pcmk_host_map', '').split(';') if entry.strip()]
        if (values.get('pcmk_host_check') != 'static-list' or not hosts or not hosts <= members
                or any(len(entry) != 2 or not entry[1].strip() for entry in entries)
                or len({entry[0] for entry in entries}) != len(entries)
                or {entry[0] for entry in entries} != hosts
                or len({entry[1] for entry in entries}) != len(entries)):
            return False, False
        endpoint = values.get('ipaddr', values.get('multicast_address', '')).strip().lower()
        agent = item.get('type')
        if agent in HTTPS_FENCERS:
            # A running fencer is insufficient evidence if its management
            # credential would be sent over an unverified TLS connection.
            if (not endpoint or {'ssl_insecure', 'ssl-insecure'} & values.keys()
                    or not any(enabled(values.get(key)) for key in ('ssl', 'ssl_secure', 'ssl-secure'))):
                return False, False
        if agent in ('fence_pve', 'fence_proxypermit_pve') and values.get('vmtype', 'qemu') not in ('qemu', 'lxc'):
            return False, False
        if agent == 'fence_ipmilan':
            # One directly addressed BMC controls one physical chassis.
            if (not endpoint or len(hosts) != 1 or 'target' in values or values.get('lanplus') != '1'
                    or values.get('cipher') != '17' or values.get('method', 'onoff') != 'onoff'):
                return False, False
        for host, target in entries:
            target = target.strip()
            if agent in ('fence_pve', 'fence_proxypermit_pve'):
                if not re.fullmatch(r'[1-9][0-9]{2,8}', target):
                    return False, False
            normalized = 'fence_pve' if agent == 'fence_proxypermit_pve' else agent
            default_port = {'fence_ipmilan': '623', 'fence_pve': '8006', 'fence_vmware_rest': '443'}.get(normalized, '')
            identity = (normalized, endpoint, values.get('ipport', default_port),
                        values.get('vmtype', 'qemu') if normalized == 'fence_pve' else '',
                        '' if agent == 'fence_ipmilan' else target)
            if identity in targets and targets[identity] != host:
                return False, False
            targets[identity] = host
        covered.update(hosts)
        if item.get('id') in ready:
            available.update(hosts)
    return covered == members and len(members) == 2, available == members and len(members) == 2


def summarize(cib_text, monitor_text, device_text, crypto_text, local_vip, local_name, *,
              quorum_text, native_credential='unknown'):
    cib, monitor = xml(cib_text), xml(monitor_text)
    configuration = cib.find('configuration')
    if configuration is None:
        raise ValueError('Missing cluster configuration.')
    options = properties(configuration.find('crm_config'))
    resource = configuration.find(f"resources/primitive[@id='{RESOURCE}']")
    resource_options = properties(resource.find('meta_attributes')) if resource is not None else {}
    primitives = configuration.findall('.//primitive')
    fencers = [item for item in primitives if item.get('class') == 'stonith' and item.get('type') in FENCERS]
    agents = {item.get('id'): 'stonith:' + item.get('type') for item in fencers}
    ready = {item.get('id') for item in monitor.findall('resources/resource')
        if item.get('id') in agents
        and item.get('resource_agent') == agents[item.get('id')] and enabled(item.get('active'))
        and enabled(item.get('managed'))
        and not any(enabled(item.get(key)) for key in ('failed', 'blocked', 'orphaned', 'maintenance'))}
    members = {node.get('uname') for node in configuration.findall('nodes/node')}
    fencing_configured, fencer_ready = fencing_coverage(fencers, ready, members)
    credential_status = {}
    if any(item.get('type') == 'fence_proxypermit_pve' for item in fencers):
        if native_credential not in ('current', 'expired', 'unknown'):
            native_credential = 'unknown'
        credential_status['localFencingCredential'] = native_credential
        fencer_ready = fencer_ready and native_credential == 'current'
    policy_safe = bool(enabled(options.get('stonith-enabled')) and enabled(options.get('startup-fencing'))
        and len(configuration.findall('nodes/node')) == 2
        and options.get('no-quorum-policy') == 'stop'
        and not enabled(options.get('maintenance-mode')) and not enabled(options.get('stop-all-resources'))
        and resource is not None and resource.get('class') == 'ocf' and resource.get('provider') == 'proxypermit'
        and len(resource.findall('meta_attributes')) == 1
        and resource.get('type') == 'vip' and resource_options.get('requires') == 'fencing'
        and re.search(r'^totem\.crypto_cipher \(str\) = aes(?:128|192|256)$', crypto_text, re.M)
        and re.search(r'^totem\.crypto_hash \(str\) = sha(?:256|384|512)$', crypto_text, re.M))
    dc = monitor.find('summary/current_dc')
    quorate = bool(dc is not None and enabled(dc.get('present')) and enabled(dc.get('with_quorum'))
        and re.search(r'^Quorate:[ \t]+Yes[ \t]*$', quorum_text, re.M))
    # qdevice can retain an earlier expected-vote snapshot after rejoining.
    expected_witness = (re.search(r'^Expected votes:[ \t]+3[ \t]*$', quorum_text, re.M)
        and re.search(r'^Quorum:[ \t]+2[ \t]*$', quorum_text, re.M)
        and re.search(r'^Flags:[ \t]+[^\n]*\bQdevice\b', quorum_text, re.M))
    witness = bool(expected_witness and re.search(r'^State:\s+Connected\s*$', device_text, re.M)
        and re.search(r'^TLS active:\s+Yes \(client certificate sent\)\s*$', device_text, re.M))
    reported = monitor.find(f"resources/resource[@id='{RESOURCE}']")
    if reported is None:
        return dict(state='unknown', owners=0, quorate=quorate, witnessConnected=witness,
                    fencingConfigured=fencing_configured, fencingReady=fencer_ready, policySafe=False, **credential_status)
    nodes = reported.findall('node')
    owners = max(int(reported.get('nodes_running_on', '0')), len(nodes))
    if not 0 <= owners <= 2:
        raise ValueError('Unexpected cluster resource ownership.')
    local_owner = any(node.get('name') == local_name for node in nodes)
    foreign_vip = local_vip and not local_owner
    healthy_resource = (enabled(reported.get('active')) and enabled(reported.get('managed'))
        and not any(enabled(reported.get(key)) for key in ('failed', 'blocked', 'orphaned', 'maintenance')))
    if owners > 1 or foreign_vip:
        state = 'unsafe'
    elif owners == 0:
        state = 'stopped'
    elif (len(nodes) != 1 or nodes[0].get('name') not in members
          or local_name not in members or local_owner != local_vip
          or not (policy_safe and quorate and witness and fencing_configured and fencer_ready and healthy_resource)):
        state = 'degraded'
    else:
        state = 'protected'
    return dict(state=state, owners=max(owners, 1 if local_vip else 0), quorate=quorate,
                witnessConnected=witness, fencingConfigured=fencing_configured, fencingReady=fencer_ready,
                policySafe=policy_safe, **credential_status)
