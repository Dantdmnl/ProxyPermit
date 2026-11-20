"""Bounded peer-key transitions. Callers serialize state with the pairing lock."""
import hmac
import re
import secrets
import time

from .tinyproxy import ConfigError

TRANSITION_SECONDS = 600


def valid_key(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value))


def authorized(peer, token, now=None):
    if not isinstance(peer, dict) or not valid_key(token) or not valid_key(peer.get('key')):
        return False
    rotation = peer.get('rotation')
    now = time.time() if now is None else now
    if rotation is not None:
        if (not isinstance(rotation, dict) or rotation.get('state') not in ('prepared', 'committed', 'complete')
                or not isinstance(rotation.get('id'), str) or not re.fullmatch(r'[0-9a-f]{32}', rotation['id'])):
            return False
        if rotation['state'] == 'prepared':
            if (type(rotation.get('expires')) is not int or type(rotation.get('created')) is not int
                    or rotation['expires'] - rotation['created'] != TRANSITION_SECONDS or not valid_key(rotation.get('key'))
                    or now >= rotation['expires']):
                return False
        elif peer.get('credentialVersion') != rotation['id']:
            return False
    return hmac.compare_digest(peer['key'], token)


def begin_rotation(peer, now=None):
    now = int(time.time() if now is None else now)
    if not isinstance(peer, dict) or not valid_key(peer.get('key')):
        raise ConfigError('Pair the nodes before rotating the peer key.')
    if not isinstance(peer.get('rotation', {}), dict):
        raise ConfigError('The peer credential state is invalid.')
    if peer.get('rotation', {}).get('state') in ('prepared', 'committed'):
        raise ConfigError('A peer-key rotation is already pending. An expired transition requires re-pairing.')
    return {**peer, 'rotation': dict(id=secrets.token_hex(16), key=secrets.token_hex(32),
                                    state='prepared', created=now, expires=now + TRANSITION_SECONDS)}


def rotation_offer(peer, now=None):
    rotation = peer.get('rotation') if isinstance(peer, dict) else None
    now = time.time() if now is None else now
    if rotation and authorized(peer, peer.get('key'), now) and rotation.get('state') == 'prepared':
        return {key: rotation[key] for key in ('id', 'key', 'expires')}
    return None


def validate_offer(offer, now=None):
    now = time.time() if now is None else now
    if (not isinstance(offer, dict) or not isinstance(offer.get('id'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', offer['id']) or not valid_key(offer.get('key'))
            or type(offer.get('expires')) is not int or not now < offer['expires'] <= now + TRANSITION_SECONDS + 60):
        raise ConfigError('The peer-key transition is invalid or expired.')
    return {key: offer[key] for key in ('id', 'key', 'expires')}


def commit_rotation(peer, token, identifier, now=None):
    now = int(time.time() if now is None else now)
    if not isinstance(peer, dict) or not valid_key(token):
        raise ConfigError('The peer-key transition could not be verified.')
    rotation = peer.get('rotation') or {}
    if not isinstance(rotation, dict):
        raise ConfigError('The peer-key transition could not be verified.')
    if not isinstance(identifier, str) or rotation.get('id') != identifier:
        raise ConfigError('The peer-key transition could not be verified.')
    if rotation.get('state') in ('committed', 'complete') and authorized(peer, token, now):
        return peer  # A lost commit response must be recoverable after the deadline.
    if (rotation.get('state') != 'prepared' or not authorized(peer, peer.get('key'), now)
            or not hmac.compare_digest(rotation['key'], token)):
        raise ConfigError('The peer-key transition could not be verified.')
    return {**peer, 'key': token, 'credentialVersion': identifier,
            'rotation': dict(id=identifier, state='committed', committed=now)}


def confirm_rotation(peer, token, identifier, now=None):
    """Allow another rotation only after B has used its persisted active key."""
    rotation = peer.get('rotation') if isinstance(peer, dict) else None
    if (not authorized(peer, token, now) or not rotation or rotation.get('state') != 'committed'
            or identifier != peer.get('credentialVersion')):
        return peer
    return {**peer, 'rotation': {**rotation, 'state': 'complete',
                                'completed': int(time.time() if now is None else now)}}


def public_status(peer, now=None):
    rotation = peer.get('rotation') if isinstance(peer, dict) else None
    if not rotation:
        return dict(state=peer.get('credentialState', 'idle') if isinstance(peer, dict) else 'idle',
                    completed=peer.get('credentialChanged') if isinstance(peer, dict) else None)
    if not isinstance(rotation, dict):
        return dict(state='invalid')
    now = time.time() if now is None else now
    state = rotation.get('state', 'prepared')
    if state == 'prepared':
        if type(rotation.get('expires')) is not int:
            state = 'invalid'
        elif now >= rotation['expires']:
            state = 'expired'
    return dict(state=state, expires=rotation.get('expires'), completed=rotation.get('completed'))
