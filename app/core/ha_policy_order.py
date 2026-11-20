"""Membership-scoped publication ordering; a content hash alone cannot order history."""
import re
import secrets

from .tinyproxy import ConfigError


class PolicyOrderError(ConfigError):
    """A peer is serving an older publication or a different membership epoch."""


def valid(value):
    return (isinstance(value, dict) and isinstance(value.get('epoch'), str)
            and re.fullmatch(r'[0-9a-f]{32}', value['epoch']) is not None
            and type(value.get('sequence')) is int and 1 <= value['sequence'] <= 2 ** 53 - 1)


def next_publication(previous, revision):
    if not previous or previous.get('generation') is None:
        return dict(epoch=secrets.token_hex(16), sequence=1)
    old = previous['generation']
    if not valid(old):
        raise PolicyOrderError('The stored HA publication generation is invalid.')
    if previous.get('revision') == revision:
        return dict(old)
    if old['sequence'] == 2 ** 53 - 1:
        raise PolicyOrderError('The HA publication sequence is exhausted. Review cluster recovery.')
    return dict(epoch=old['epoch'], sequence=old['sequence'] + 1)


def verify(policy, previous):
    incoming = policy.get('generation')
    old = (previous or {}).get('generation')
    if incoming is not None and not valid(incoming):
        raise PolicyOrderError('The HA publication generation is invalid.')
    if old is None:
        return  # Upgrade compatibility ends once a versioned policy was accepted.
    if not valid(old) or incoming is None or incoming['epoch'] != old['epoch']:
        raise PolicyOrderError('The HA publication membership changed. Review recovery before accepting policy.')
    if (incoming['sequence'] < old['sequence'] or
            (incoming['sequence'] == old['sequence'] and policy['revision'] != previous['revision'])):
        raise PolicyOrderError('The primary HA node is serving an older or conflicting publication.')
