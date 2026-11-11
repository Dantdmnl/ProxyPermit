"""Read Linux TCP listener state without opening dummy client connections.

This checks the listener, not end-to-end proxy forwarding. It deliberately
does not require root, inspect process command lines or read configuration.
The same source is embedded in the root-owned Keepalived health helper.
"""
from pathlib import Path


def tcp_listener(port):
    """Return whether a local IPv4/IPv6 TCP port is listening; fail closed."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Invalid TCP port.')
    for name in ('tcp', 'tcp6'):
        try:
            with (Path('/proc/net') / name).open(encoding='ascii') as table:
                next(table, None)
                for line in table:
                    columns = line.split()
                    if len(columns) < 4 or columns[3] != '0A':
                        continue
                    try:
                        local_port = int(columns[1].rsplit(':', 1)[1], 16)
                    except (ValueError, IndexError):
                        continue
                    if local_port == port:
                        return True
        except FileNotFoundError:
            # IPv6 tables can be absent when the protocol is disabled.
            continue
    return False
