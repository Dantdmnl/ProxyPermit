"""Portable Tinyproxy policy and signed, single-use transfer envelopes."""
import hashlib
import hmac
import json
import re
import secrets
import time

from .tinyproxy import ConfigError, parse, revision


PORTABLE = frozenset({
    'timeout', 'maxclients', 'allow', 'deny', 'connectport', 'filtertype',
    'filterurls', 'filtercasesensitive', 'filterdefaultdeny', 'filterextended', 'loglevel',
})
MAX_TRANSFER = 4 * 1024 * 1024


def baseline(config, filters):
    return [revision(config), revision(filters)]


def portable_config(content):
    lines = content.splitlines()
    return '\n'.join(lines[row['line'] - 1] for row in parse(content) if row['name'].lower() in PORTABLE)


def merge_policy(local_config, portable):
    if any(row['name'].lower() not in PORTABLE for row in parse(portable)):
        raise ConfigError('The transfer contains a host-specific Tinyproxy directive.')
    if portable == portable_config(local_config):
        return local_config
    lines = local_config.splitlines()
    positions = {row['line'] for row in parse(local_config) if row['name'].lower() in PORTABLE}
    first = min(positions, default=len(lines) + 1)
    before = lines[:first - 1]
    after = [line for number, line in enumerate(lines[first - 1:], first) if number not in positions]
    return '\n'.join(before + portable.splitlines() + after) + '\n'


def issue_invite(cluster, current):
    return dict(cluster=cluster, baseline=current, key=secrets.token_hex(32), expires=int(time.time()) + 600)


def parse_invite(text, cluster):
    if not isinstance(text, str) or len(text) > 1024:
        raise ConfigError('Invalid HA invitation.')
    try:
        invite = json.loads(text)
        if (not isinstance(invite, dict) or set(invite) != {'cluster', 'baseline', 'key', 'expires'}
                or invite['cluster'] != cluster or type(invite['expires']) is not int
                or not time.time() < invite['expires'] <= time.time() + 660
                or not isinstance(invite['key'], str) or not re.fullmatch(r'[0-9a-f]{64}', invite['key'])
                or not isinstance(invite['baseline'], list) or len(invite['baseline']) != 2
                or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in invite['baseline'])):
            raise ValueError
        return invite
    except (ValueError, RecursionError):
        raise ConfigError('The HA invitation is invalid, expired, or belongs to another cluster.') from None


def sign_policy(invite, config, filters):
    payload = json.dumps(dict(cluster=invite['cluster'], baseline=invite['baseline'],
                              config=portable_config(config), filters=filters), separators=(',', ':'))
    return json.dumps(dict(payload=payload, signature=_signature(invite, payload)), separators=(',', ':'))


def _signature(invite, payload):
    return hmac.new(bytes.fromhex(invite['key']), b'proxypermit-ha-policy-v1\0' + payload.encode(), hashlib.sha256).hexdigest()


def verify_policy(text, invite):
    if not isinstance(text, str) or len(text) > MAX_TRANSFER:
        raise ConfigError('Invalid HA policy transfer.')
    try:
        envelope = json.loads(text)
        payload, signature = envelope['payload'], envelope['signature']
        if not isinstance(payload, str) or not isinstance(signature, str) or not re.fullmatch(r'[0-9a-f]{64}', signature):
            raise ValueError
        if not hmac.compare_digest(_signature(invite, payload), signature):
            raise ValueError
        policy = json.loads(payload)
        if (policy['cluster'] != invite['cluster'] or policy['baseline'] != invite['baseline']
                or not isinstance(policy['config'], str) or not isinstance(policy['filters'], str)):
            raise ValueError
        return policy
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ConfigError('The HA policy transfer does not match the current invitation.') from None
