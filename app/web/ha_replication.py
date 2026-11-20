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
import stat
import time
from pathlib import Path

from ..core.ha_policy import MAX_TRANSFER, portable_config
from ..core.ha import ha_topology
from ..core.tinyproxy import ConfigError, atomic_write
from ..core import ha_credentials as credentials
from ..core.files import sync_directory
from ..core import ha_policy_order
from ..core import ha_certificates
from ..core.files import file_lock

CERTIFICATE_TRANSITION = Path('/etc/proxypermit/ha-certificate-transition.json')


class PeerReply(dict):
    """Keep locally verified TLS identity separate from peer-controlled JSON."""
    verified_pin = None


def certificate_transition():
    try:
        descriptor = os.open(CERTIFICATE_TRANSITION, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > 8192):
            raise ConfigError('The HA certificate transition must be protected by the OS administrator.')
        return ha_certificates.validate(json.load(source))


def state(name):
    return Path(os.getenv('SESSION_STATE_FILE', '/var/lib/proxypermit/session-state.json')).with_name('ha-' + name + '.json')


def read_state(name):
    try:
        value = json.loads(state(name).read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ConfigError('Invalid protected HA state. Review the local state files.')
        return value
    except FileNotFoundError:
        return None


def write_state(name, value):
    atomic_write(state(name), json.dumps(value))


def delete_state(name):
    path = state(name)
    path.unlink(missing_ok=True)
    if path.parent.exists():
        sync_directory(path.parent)


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
    with file_lock(state('publication-lock')):
        payload['generation'] = ha_policy_order.next_publication(read_state('published-policy'), payload['revision'])
        write_state('published-policy', payload)


def validate_published(policy, previous=None):
    if (not isinstance(policy, dict) or policy.get('cluster') != cluster_id()
            or any(not isinstance(policy.get(key), str) for key in ('config', 'filters', 'revision'))
            or not credentials.valid_key(policy['revision'])):
        raise ConfigError('Invalid published HA policy.')
    body = {key: policy[key] for key in ('cluster', 'config', 'filters')}
    expected = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    if not hmac.compare_digest(expected, policy['revision']):
        raise ConfigError('The published HA policy revision does not match its contents.')
    ha_policy_order.verify(policy, previous)


def _connection(host, context):
    return http.client.HTTPSConnection(host, 8443, context=context, timeout=5)


def pinned_connection(host, pin):
    """Discover only the pre-pinned leaf, then validate it before sending secrets.

    The second TLS handshake checks validity dates and signatures with OpenSSL.
    No system CA or an unexpected replacement certificate is trusted.
    """
    pins = [pin] if isinstance(pin, str) else pin
    if (not isinstance(pins, (tuple, list)) or not 1 <= len(pins) <= 2
            or any(not credentials.valid_key(value) for value in pins)):
        raise ConfigError('The primary HA certificate pin is invalid.')
    discovery = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    discovery.check_hostname = False
    discovery.verify_mode = ssl.CERT_NONE
    discovery.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = _connection(host, discovery)
    try:
        connection.connect()
        certificate = connection.sock.getpeercert(binary_form=True)
        if not isinstance(certificate, bytes) or not 0 < len(certificate) <= 131072:
            raise ConfigError('The primary did not present a valid HA certificate.')
        selected_pin = hashlib.sha256(certificate).hexdigest()
        if not any(hmac.compare_digest(value, selected_pin) for value in pins):
            raise ConfigError('The primary HA certificate does not match the pairing code.')
    finally:
        connection.close()
    verified = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    verified.check_hostname = False  # Explicit leaf pin is the node identity.
    verified.minimum_version = ssl.TLSVersion.TLSv1_2
    verified.verify_flags |= getattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN', 0)
    verified.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    connection = _connection(host, verified)
    try:
        connection.connect()
        verified_certificate = connection.sock.getpeercert(binary_form=True)
        if (not isinstance(verified_certificate, bytes) or not verified_certificate
                or not hmac.compare_digest(selected_pin, hashlib.sha256(verified_certificate).hexdigest())):
            raise ConfigError('The primary HA certificate changed during verification.')
        connection.ha_pin = selected_pin
        return connection
    except BaseException:
        connection.close()
        raise


def fetch_primary(pin, key, action, applied='', payload=None, credential_version='', certificate_prepared='', certificate_version=''):
    if not credentials.valid_key(key) or action not in ('enroll', 'policy', 'credential-commit'):
        raise ConfigError('Invalid HA peer request.')
    host = str(ipaddress.IPv4Address(os.environ['NODE0_ADDRESS']))
    connection = pinned_connection(host, pin)
    try:
        connection.request('GET' if action == 'policy' else 'POST', '/internal/ha/' + action,
                           body=json.dumps(payload).encode('utf-8') if payload is not None else None,
                           headers={'Authorization': 'Bearer ' + key, 'X-HA-Applied-Revision': applied,
                                    'X-HA-Credential-Version': credential_version,
                                    'X-HA-Credential-Protocol': '1',
                                    'X-HA-Certificate-Protocol': '1',
                                    'X-HA-Certificate-Prepared': certificate_prepared,
                                    'X-HA-Certificate-Version': certificate_version,
                                    'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ConfigError('The primary HA node refused the request.')
        body = response.read(MAX_TRANSFER + 1)
        if len(body) > MAX_TRANSFER:
            raise ConfigError('The published HA policy is too large.')
        decoded = json.loads(body)
        if isinstance(decoded, dict):
            decoded = PeerReply(decoded)
            decoded.verified_pin = getattr(connection, 'ha_pin', None)
        return decoded
    except http.client.HTTPException:
        # IncompleteRead can contain a partial body, including a rotation offer.
        # Never copy that exception or body into operational logs.
        raise ConfigError('The HA peer returned an incomplete or invalid HTTP response.') from None
    finally:
        connection.close()


def finish_key_rotation(peer):
    """Recover a lost commit response using the durably staged credential."""
    offer = peer.get('rotation')
    if not offer:
        return peer
    if (not isinstance(offer, dict) or not credentials.valid_key(offer.get('key'))
            or not isinstance(offer.get('id'), str) or not re.fullmatch(r'[0-9a-f]{32}', offer['id'])):
        raise ConfigError('The stored peer-key transition is invalid.')
    # A previous attempt may have failed after rename but before directory
    # fsync. Reconfirm durable staging before asking A to revoke the old key.
    write_state('peer', peer)
    result = fetch_primary(ha_certificates.allowed_pins(peer), offer['key'], 'credential-commit', payload={'id': offer['id']})
    if (not isinstance(result, dict) or result.get('committed') is not True
            or result.get('id') != offer['id'] or result.get('cluster') != cluster_id()):
        raise ConfigError('The primary did not confirm the peer-key transition.')
    updated = {**peer, 'key': offer['key'], 'credentialVersion': offer['id'],
               'credentialState': 'committed', 'credentialChanged': int(time.time())}
    updated.pop('rotation', None)
    write_state('peer', updated)
    return updated
