"""Prepare version-checked CIB maintenance transitions without exposing credentials."""
import re
from xml.etree import ElementTree as ET

from .tinyproxy import ConfigError

LEASE_ID = 'proxypermit-maintenance-state'


class MaintenanceConflict(ConfigError):
    pass


def nodes(root):
    result = root.findall('configuration/nodes/node')
    if (len(result) != 2 or len({node.get('uname') for node in result}) != 2
            or any(not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', node.get('uname', ''))
                   or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', node.get('id', '')) for node in result)):
        raise MaintenanceConflict('Cluster maintenance requires exactly two unambiguous node identities.')
    return sorted(result, key=lambda node: node.get('uname'))


def marker(root):
    members = nodes(root)
    found = root.findall(f".//*[@id='{LEASE_ID}']")
    if not found:
        return None
    if len(found) != 1 or found[0] not in list(members[0]):
        raise MaintenanceConflict('The cluster maintenance reservation is ambiguous.')
    result = {item.get('name'): item.get('value') for item in found[0].findall('nvpair')}
    expected = {'proxypermit-maintenance-owner', 'proxypermit-maintenance-token', 'proxypermit-maintenance-created'}
    if (len(list(found[0])) != 3 or set(result) != expected
            or result['proxypermit-maintenance-owner'] not in {node.get('uname') for node in members}
            or not re.fullmatch(r'[0-9a-f]{32}', result['proxypermit-maintenance-token'])
            or not re.fullmatch(r'[0-9]{1,12}', result['proxypermit-maintenance-created'])):
        raise MaintenanceConflict('The cluster maintenance reservation is invalid.')
    return dict(node=result['proxypermit-maintenance-owner'], token=result['proxypermit-maintenance-token'])


def claim(root, local_name, token, created):
    members = nodes(root)
    if marker(root) is not None:
        raise MaintenanceConflict('Another cluster maintenance operation is reserved. Review it before continuing.')
    if (not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token)
            or type(created) is not int or not 0 <= created <= 999999999999):
        raise MaintenanceConflict('Invalid maintenance operation.')
    local = next((node for node in members if node.get('uname') == local_name), None)
    if local is None:
        raise MaintenanceConflict('The local node does not match the cluster configuration.')
    identity = local.get('id')
    if root.findall(f"status/node_state[@id='{identity}']/transient_attributes//nvpair[@name='standby']"):
        raise MaintenanceConflict('Clear transient standby overrides through OS cluster administration first.')
    pairs = local.findall("instance_attributes/nvpair[@name='standby']")
    if len(pairs) > 1 or local.findall('instance_attributes/rule'):
        raise MaintenanceConflict('Rule-driven or duplicate standby settings require OS review.')
    created_set = None
    before = pairs[0].get('value') if pairs else None
    if before not in (None, 'on', 'off', 'true', 'false'):
        raise MaintenanceConflict('The existing standby setting is unsupported.')
    if pairs:
        pair = pairs[0]
        if not pair.get('id'):
            raise MaintenanceConflict('The standby setting has no stable identity.')
    else:
        created_set = 'proxypermit-maintenance-local-' + token
        attributes = ET.SubElement(local, 'instance_attributes', id=created_set)
        pair = ET.SubElement(attributes, 'nvpair', id=created_set + '-standby', name='standby')
    pair.set('value', 'on')
    reservation = ET.SubElement(members[0], 'instance_attributes', id=LEASE_ID)
    for name, value in (('owner', local_name), ('token', token), ('created', str(created))):
        ET.SubElement(reservation, 'nvpair', id=LEASE_ID + '-' + name,
                      name='proxypermit-maintenance-' + name, value=value)
    return dict(node=local_name, token=token, standbyId=pair.get('id'), standbySet=created_set,
                before=before, created=created)


def release(root, saved):
    current = marker(root)
    if current != {key: saved.get(key) for key in ('node', 'token')}:
        raise MaintenanceConflict('The cluster maintenance reservation changed. Automatic release was refused.')
    members = nodes(root)
    local = next(node for node in members if node.get('uname') == saved['node'])
    if (root.findall(f"status/node_state[@id='{local.get('id')}']/transient_attributes//nvpair[@name='standby']")
            or local.findall('instance_attributes/rule')
            or len(local.findall("instance_attributes/nvpair[@name='standby']")) != 1):
        raise MaintenanceConflict('The standby policy changed during maintenance. OS review is required.')
    pairs = [pair for pair in local.findall('instance_attributes/nvpair') if pair.get('id') == saved.get('standbyId')]
    if len(pairs) != 1 or pairs[0].get('name') != 'standby' or pairs[0].get('value') != 'on':
        raise MaintenanceConflict('The standby setting changed during maintenance. Preserve the operator change.')
    if saved.get('before') is None:
        container = next((item for item in local.findall('instance_attributes') if item.get('id') == saved.get('standbySet')), None)
        if container is None or list(container) != pairs:
            raise MaintenanceConflict('The temporary standby settings changed during maintenance.')
        local.remove(container)
    elif saved['before'] in ('on', 'off', 'true', 'false'):
        pairs[0].set('value', saved['before'])
    else:
        raise MaintenanceConflict('The stored standby recovery setting is invalid.')
    members[0].remove(members[0].find(f"instance_attributes[@id='{LEASE_ID}']"))
