"""Validate the shared two-node HA topology."""
import ipaddress
import re


def ha_topology(node, interface, node_a, node_b, vip, clients, vrid):
    if node not in ('a', 'b') or not interface or not re.fullmatch(r'[A-Za-z0-9_.][A-Za-z0-9_.-]{0,14}', interface):
        raise ValueError('Choose HA node a or b and provide a valid network interface.')
    if not all((node_a, node_b, vip, clients)) or '/' not in vip or '/' not in clients:
        raise ValueError('Provide both node IP addresses, the virtual IP with its prefix, and the allowed client network.')
    a, b = ipaddress.IPv4Address(node_a), ipaddress.IPv4Address(node_b)
    virtual = ipaddress.IPv4Interface(vip)
    allowed = ipaddress.IPv4Network(clients)
    network = virtual.network
    addresses = (a, b, virtual.ip)
    if len(set(addresses)) != 3 or any(
        addr not in network or addr in (network.network_address, network.broadcast_address)
        or addr.is_loopback or addr.is_multicast or addr.is_unspecified or addr.is_link_local
        for addr in addresses
    ):
        raise ValueError('Both nodes and the virtual IP must have distinct usable IPv4 addresses in the same subnet.')
    if allowed.prefixlen == 0 or not 1 <= vrid <= 255:
        raise ValueError('Choose a restricted client network and a VRRP ID between 1 and 255.')
    return dict(node=node, interface=interface, nodeA=str(a), nodeB=str(b), vip=str(virtual),
                clients=str(allowed), vrid=vrid, local=str(a if node == 'a' else b), peer=str(b if node == 'a' else a))


def ha_firewall_rules(topology):
    """Build UFW argument lists from a validated HA topology."""
    interface = topology['interface']
    local, peer = topology['local'], topology['peer']
    vip = topology['vip'].split('/', 1)[0]
    return [
        ['allow', 'in', 'on', interface, 'from', topology['clients'], 'to', vip, 'port', '3128', 'proto', 'tcp',
         'comment', 'ProxyPermit HA IN - Tinyproxy clients to virtual IP'],
        ['allow', 'in', 'on', interface, 'from', peer, 'to', local, 'proto', 'vrrp',
         'comment', 'ProxyPermit HA IN - Keepalived VRRP from peer'],
        ['allow', 'out', 'on', interface, 'from', local, 'to', peer, 'proto', 'vrrp',
         'comment', 'ProxyPermit HA OUT - Keepalived VRRP to peer'],
    ]
