"""Bounded local Tinyproxy responsiveness probe; no external test website.

Embedded in the root-owned Keepalived helper. Run as the existing service
account to read the protected native configuration, never as root.
"""
import re
import shlex
import socket
import time
from pathlib import Path

HEALTH_PATH = '/__proxypermit_health__'


def probe_settings(configuration):
    port, host = 3128, 'tinyproxy.stats'
    with Path(configuration).open(encoding='utf-8-sig') as source:
        for line in source:
            parts = shlex.split(line, comments=True)
            if len(parts) == 2 and parts[0].lower() == 'port':
                port = int(parts[1])
            if len(parts) == 2 and parts[0].lower() == 'stathost':
                host = parts[1]
    if not 1 <= port <= 65535 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', host):
        raise ValueError('Invalid local health-check settings.')
    return port, host


def proxy_responsive(configuration, timeout=0.7):
    """A listener must answer HTTP before the deadline, including auth denials."""
    try:
        port, host = probe_settings(configuration)
        target = '[' + host + ']' if ':' in host else host
        request = (f'GET http://{target}{HEALTH_PATH} HTTP/1.0\r\nHost: {host}\r\n'
                   'User-Agent: ProxyPermit-Health/1\r\nConnection: close\r\n\r\n').encode('ascii')
        deadline = time.monotonic() + timeout
        with socket.create_connection(('127.0.0.1', port), timeout=timeout) as connection:
            connection.sendall(request)
            received = bytearray()
            while len(received) <= 1024 * 1024:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                connection.settimeout(remaining)
                chunk = connection.recv(16384)
                if not chunk:
                    break
                received.extend(chunk)
            else:
                return False
        return bool(b'\r\n\r\n' in received and re.match(rb'HTTP/1\.[01] (?:200|401|403|407) ', received))
    except (OSError, ValueError, UnicodeError):
        return False
