# Architecture and HA

[Nederlands](../nl/architecture.md) · [Documentation](README.md)

## Current design

ProxyPermit runs a Flask management console alongside Tinyproxy. The console
handles accounts, filter rules, configuration drafts, and logs. Tinyproxy
handles client traffic. The management console has its own listening address
and access controls. The proxy configuration editor validates the structure
and consistency of native configuration. Available directives depend on the
Tinyproxy build. HTTPS CONNECT exposes the tunnel destination and port, while
the encrypted HTTP paths and headers inside the tunnel remain opaque.

The installer supports one node or two nodes with Keepalived. New HA deployments
manage policy per node. Upgrades can retain existing legacy synchronization.
The application separates staged drafts from active configuration and restores
the previous files when activation fails. Service health after activation and
successful client traffic are separate checks.

## Web server

Gunicorn is the default WSGI server. Granian is an optional Rust HTTP server
running the same Flask application. The entry point for both is
`python -m app.runtime.server`. Choose Granian with `--server granian` or install
`requirements/granian.txt` and set `APP_SERVER=granian`. Granian uses one
blocking thread per worker. Performance depends on workload and needs a
representative benchmark.

Use a reverse proxy to terminate TLS with Granian. Existing Gunicorn deployments
can use direct TLS. For a Granian Unix socket, grant the reverse-proxy worker
access through the appropriate group with socket permissions of `0660`.

## High availability

| Layer | Component | Responsibility |
| --- | --- | --- |
| Local process recovery | systemd | Restarts the manager or proxy on the same host. |
| Virtual IP failover | Keepalived / VRRP | Moves the proxy address between nodes based on health. |
| TCP load balancing | HAProxy, if required | Distributes connections when multiple active backends are needed. |
| VM recovery | Proxmox HA, if used | Can restart a VM elsewhere after host loss. |
| Cluster resource management | Pacemaker / Corosync, if required | Requires explicit quorum and fencing design. |
| Policy distribution | Local publication or legacy filter sync | New clusters publish on each node. Legacy sync covers filter rules. |

VRRP controls address ownership. It cannot enforce a single policy writer
during a network partition. Legacy bidirectional sync also cannot establish
consensus. The proposed policy distribution model uses one authoritative source,
versioned releases, and a controlled rollout. Each node keeps its last active
configuration if management is unreachable. Publication rights and management
failover still need a design.

## Roadmap and verification

1. Keep software rollback separate from policy rollback and verify client
   traffic during activation.
2. Extend the forms for client networks, destination policy, and upstreams.
   Preserve comments and order and protect credentials in shareable output.
3. Track rollout health and configuration versions per node.
4. Validate process failure, host loss, and network partitions on separate hosts.

`python scripts/dev/test.py` uses isolated fixture files. Run
`python scripts/dev/check_server.py --backend gunicorn` or `--backend granian`
to test a real server on a temporary loopback port. The
[verification report](verification.md) distinguishes WSL lab tests from checks
that still require separate hosts.
