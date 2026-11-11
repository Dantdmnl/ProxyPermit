"""Read local Linux network inventory without changing interfaces or probing hosts."""
import ipaddress
import json
import re
import subprocess


def ip_json(*arguments):
    response = subprocess.run(['ip', '-j', '-4', *arguments], check=True,
                              capture_output=True, text=True, timeout=3)
    if len(response.stdout) > 1024 * 1024:
        raise ValueError('Network inventory is too large.')
    value = json.loads(response.stdout)
    if not isinstance(value, list):
        raise ValueError('Invalid network inventory.')
    return value


def discover_network():
    addresses = ip_json('address', 'show')
    routes = ip_json('route', 'show')
    neighbors = ip_json('neigh', 'show')
    occupied = {str(item.get('gateway', '')) for item in routes}
    occupied.update(str(item.get('dst', '')) for item in neighbors)
    occupied.update(str(address.get('local', '')) for item in addresses for address in item.get('addr_info', []))
    networks = []
    for interface in addresses:
        name, flags = interface.get('ifname', ''), interface.get('flags', [])
        if (not re.fullmatch(r'[A-Za-z0-9_.][A-Za-z0-9_.-]{0,14}', name)
                or 'UP' not in flags or 'LOOPBACK' in flags
                or interface.get('link_type', 'ether') != 'ether'):
            continue
        for address in interface.get('addr_info', []):
            try:
                local = ipaddress.IPv4Interface(f"{address['local']}/{address['prefixlen']}")
            except (KeyError, ValueError):
                continue
            if local.ip.is_loopback or local.ip.is_link_local or local.ip.is_multicast or not 1 <= local.network.prefixlen <= 29:
                continue
            candidate = next((str(local.network.network_address + offset)
                              for offset in [10, *range(2, min(local.network.num_addresses - 1, 255))]
                              if offset < local.network.num_addresses - 1
                              and str(local.network.network_address + offset) not in occupied), None)
            networks.append(dict(interface=name, address=str(local.ip), prefix=local.network.prefixlen,
                                 network=str(local.network), dynamic=bool(address.get('dynamic')),
                                 suggestedVip=f'{candidate}/{local.network.prefixlen}' if candidate else None))
    return networks
