"""Bounded pin handover learned through the previously authenticated TLS peer."""
import re
import time

from .ha_credentials import valid_key
from .tinyproxy import ConfigError


def validate(offer):
    if (not isinstance(offer, dict) or not isinstance(offer.get('id'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', offer['id'])
            or any(not valid_key(offer.get(key)) for key in ('oldPin', 'newPin'))
            or offer['oldPin'] == offer['newPin'] or offer.get('phase') not in ('offered', 'committed')
            or type(offer.get('created')) is not int or type(offer.get('expires')) is not int
            or offer['expires'] - offer['created'] != 600):
        raise ConfigError('The HA certificate transition is invalid.')
    return {key: offer[key] for key in ('id', 'oldPin', 'newPin', 'phase', 'created', 'expires')}


def stage(peer, offer, observed_pin, now=None):
    offer = validate(offer)
    now = time.time() if now is None else now
    if not valid_key(peer.get('pin')) or observed_pin not in allowed_pins(peer, now):
        raise ConfigError('The HA certificate transition was not received through a trusted pin.')
    if peer.get('certificateVersion') == offer['id'] and peer['pin'] == offer['newPin']:
        return peer
    if offer['phase'] == 'committed' and observed_pin == peer['pin'] == offer['newPin']:
        # A fresh pairing can already trust the new leaf directly. Record its
        # version without reintroducing the previous pin or extending a window.
        updated = {**peer, 'certificateVersion': offer['id']}
        updated.pop('certificatePending', None)
        return updated
    if (offer['expires'] <= now or offer['created'] > now + 60):
        return prune(peer, now)
    if offer['oldPin'] != peer['pin']:
        raise ConfigError('The HA certificate transition does not match the active peer pin.')
    previous = peer.get('certificatePending')
    if previous and previous.get('id') != offer['id'] and previous.get('expires', 0) > now:
        raise ConfigError('An HA certificate transition is already pending.')
    pending = {key: offer[key] for key in ('id', 'oldPin', 'newPin', 'created', 'expires')}
    return {**peer, 'certificatePending': pending}


def allowed_pins(peer, now=None):
    now = time.time() if now is None else now
    current = peer.get('pin')
    if not valid_key(current):
        raise ConfigError('The active HA certificate pin is invalid.')
    pins = [current]
    pending = peer.get('certificatePending')
    if pending is not None:
        if not isinstance(pending, dict):
            raise ConfigError('The staged HA certificate is invalid.')
        validate({**pending, 'phase': 'offered'})
        if pending['oldPin'] != current:
            raise ConfigError('The staged HA certificate does not match the active pin.')
        if pending['created'] <= now + 60 and now < pending['expires']:
            pins.append(pending['newPin'])
    return pins


def promote(peer, offer, observed_pin, now=None):
    offer = validate(offer)
    now = time.time() if now is None else now
    pending = peer.get('certificatePending')
    pins = allowed_pins(peer, now)
    if not pending or offer['phase'] != 'committed' or observed_pin != pending['newPin']:
        return peer
    if ({key: offer[key] for key in ('id', 'oldPin', 'newPin', 'created', 'expires')} != pending
            or observed_pin not in pins or now >= pending['expires']):
        raise ConfigError('The HA certificate transition could not be confirmed.')
    updated = {**peer, 'pin': observed_pin, 'certificateVersion': offer['id']}
    updated.pop('certificatePending', None)
    return updated


def prune(peer, now=None):
    now = time.time() if now is None else now
    pending = peer.get('certificatePending')
    if isinstance(pending, dict) and type(pending.get('expires')) is int and now >= pending['expires']:
        updated = dict(peer)
        updated.pop('certificatePending', None)
        return updated
    return peer
