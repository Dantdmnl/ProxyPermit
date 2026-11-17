"""Bounded read-only HTTPS control state shared by the web app and root helper."""
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess

from . import management_tls

ENVIRONMENT = Path('/etc/proxypermit/.env')
CAPABILITY = Path('/etc/proxypermit/management-tls-web.json')
REQUEST = Path('/var/lib/proxypermit/management-tls-web-request.json')
RESULT_FOLDER = Path('/run/proxypermit-management-tls-web')
RESULT = RESULT_FOLDER / 'result.json'
UNIT = 'proxypermit-management-tls-web.service'
COMMAND = ['sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', UNIT]


def root_text(path, maximum=16384):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > maximum):
            raise ValueError('Untrusted HTTPS control metadata.')
        return source.read()


def available():
    try:
        return json.loads(root_text(CAPABILITY)) == {'version': 1}
    except (OSError, ValueError):
        return False


def unit_busy():
    """A completed result may briefly precede the oneshot service exiting."""
    result = subprocess.run(['/usr/bin/systemctl', 'show', '--property=ActiveState', '--value', UNIT],
                            check=True, capture_output=True, text=True, timeout=3)
    return result.stdout.strip() not in ('inactive', 'failed')


def approved_names(values):
    """Only OS identities and previously OS-approved certificate names are issuable."""
    approved = {'DNS:localhost', 'IP:127.0.0.1', 'IP:::1'}
    hostname = socket.gethostname().rstrip('.').lower()
    if re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', hostname):
        approved.add('DNS:' + hostname)
    result = subprocess.run(['/usr/sbin/ip', '-j', 'address', 'show'], check=True, capture_output=True,
                            text=True, timeout=3, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    for interface in json.loads(result.stdout):
        for address in interface.get('addr_info', []):
            if address.get('family') in ('inet', 'inet6'):
                value = ipaddress.ip_address(address['local'])
                if not value.is_unspecified and not value.is_multicast and not value.is_link_local:
                    approved.add('IP:' + str(value))
    if values.get('PROXYPERMIT_MANAGED_HA') == 'true' and values.get('VIP_ADDRESS'):
        approved.add('IP:' + str(ipaddress.IPv4Address(values['VIP_ADDRESS'])))
    cert = Path(values.get('MANAGER_CERT', ''))
    if cert.parent.parent == management_tls.FOLDER and re.fullmatch(r'[0-9a-f]{32}', cert.parent.name):
        previous = json.loads(root_text(cert.with_name('info.json')))
        approved.update(previous['sans'])
    if len(approved) > 128:
        raise ValueError('Too many management identities. OS review is required.')
    return sorted(approved)


def snapshot():
    from dotenv import dotenv_values
    text = root_text(ENVIRONMENT, 1024 * 1024)
    values = {key: value or '' for key, value in dotenv_values(stream=io.StringIO(text), interpolate=False).items()}
    names = approved_names(values)
    revision = hashlib.sha256((hashlib.sha256(text.encode()).hexdigest() + json.dumps(names)).encode()).hexdigest()
    managed = management_tls.status(values)
    direct_tls = values.get('GUNICORN_USE_TLS', '').lower() in ('true', '1', 'yes') or (
        not values.get('GUNICORN_USE_TLS') and values.get('MANAGER_SCHEME') == 'https')
    return dict(revision=revision, names=names, managed=bool(managed), certificate=managed,
                canEnable=not direct_tls and not managed and values.get('MANAGER_SCHEME', 'http') == 'http', canRenew=bool(managed),
                node=socket.gethostname(), role=values.get('PROXYPERMIT_HA_NODE', 'standalone'))


def result():
    try:
        value = json.loads(root_text(RESULT, 32768))
        if (not isinstance(value, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(value.get('id', '')))
                or value.get('phase') not in ('running', 'prepared', 'complete', 'failed')
                or type(value.get('created')) is not int):
            raise ValueError('Invalid HTTPS job status.')
        return value
    except FileNotFoundError:
        return None
