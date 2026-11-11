"""Pull active proxy policy over a narrowly scoped, pinned TLS channel."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import ssl
import time
from pathlib import Path

from ..core.ha_policy import MAX_TRANSFER, portable_config
from ..core.ha import ha_topology
from ..core.tinyproxy import ConfigError, atomic_write


def state(name):
    return Path(os.getenv('SESSION_STATE_FILE', '/var/lib/proxypermit/session-state.json')).with_name('ha-' + name + '.json')


def read_state(name):
    try:
        return json.loads(state(name).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


def write_state(name, value):
    atomic_write(state(name), json.dumps(value))


def cluster_id():
    return '/'.join(str(ipaddress.IPv4Address(os.environ[key])) for key in ('NODE0_ADDRESS', 'NODE1_ADDRESS', 'VIP_ADDRESS'))


def new_pairing_code():
    pin = os.getenv('HA_POLICY_TLS_PIN', '').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', pin):
        raise ConfigError('Configure the HA replication TLS certificate on node A first.')
    invite = dict(cluster=cluster_id(), pin=pin, key=secrets.token_hex(32), expires=int(time.time()) + 600)
    if os.getenv('PROXYPERMIT_HA_INTERFACE'):
        invite['topology'] = ha_topology('a', os.environ['PROXYPERMIT_HA_INTERFACE'], os.environ['NODE0_ADDRESS'],
            os.environ['NODE1_ADDRESS'], os.environ['VIP_ADDRESS'] + '/' + os.environ['HA_VIP_PREFIX'],
            os.environ['HA_CLIENT_CIDR'], int(os.environ['HA_VRID']))
    write_state('enrollment', invite)
    return json.dumps(invite, separators=(',', ':'))


def parse_pairing_code(code, expected_cluster=None):
    try:
        if not isinstance(code, str) or len(code) > 2048:
            raise ValueError
        invite = json.loads(code)
        if (not isinstance(invite, dict) or invite.get('cluster') != (expected_cluster or cluster_id())
                or type(invite.get('expires')) is not int or not time.time() < invite['expires'] <= time.time() + 660
                or any(not isinstance(invite.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', invite[key])
                       for key in ('pin', 'key'))):
            raise ValueError
        return invite
    except (ValueError, RecursionError):
        raise ConfigError('The HA pairing code is invalid, expired, or for another cluster.') from None


def snapshot(config, filters):
    payload = dict(cluster=cluster_id(), config=portable_config(config), filters=filters)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    if len(encoded) > MAX_TRANSFER - 1024:
        raise ConfigError('The published HA policy is too large.')
    payload['revision'] = hashlib.sha256(encoded).hexdigest()
    write_state('published-policy', payload)


def fetch_primary(pin, key, action, applied=''):
    host = str(ipaddress.IPv4Address(os.environ['NODE0_ADDRESS']))
    # The certificate fingerprint from the invitation is the trust anchor.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(host, 8443, context=context, timeout=5)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(pin, actual):
            raise ConfigError('The primary HA certificate does not match the pairing code.')
        connection.request('POST' if action == 'enroll' else 'GET', '/internal/ha/' + action,
                           headers={'Authorization': 'Bearer ' + key, 'X-HA-Applied-Revision': applied})
        response = connection.getresponse()
        if response.status != 200:
            raise ConfigError('The primary HA node refused the request.')
        body = response.read(MAX_TRANSFER + 1)
        if len(body) > MAX_TRANSFER:
            raise ConfigError('The published HA policy is too large.')
        return json.loads(body)
    finally:
        connection.close()
