# Verification report

[Nederlands](../nl/verification.md) · [Documentation](README.md)

## 6 November 2026

Ubuntu 26.04.1 LTS on WSL 2 ran systemd, Python 3.14.4, and Tinyproxy 1.11.3.
The tests covered Gunicorn 26.2.0 and Granian 2.8.7 with two workers, real HTTP
requests, sign-in, configuration writes, CSRF, revision conflicts, and assets.
The installer deployed Granian, upgraded to Gunicorn while preserving settings,
and rolled back to the previous release. Tinyproxy returned HTTP 403 for a
blocked destination and HTTP 200 for an allowed destination. File writes and
restarts worked under the service account.

A listener configured only in the systemd unit was lost on upgrade. The
installer now migrates it into the environment and probes the configured HTTP
endpoint, Unix socket, or local TLS endpoint. The TLS probe checks reachability.

The initial run passed 44 Python tests. After reorganizing the repository,
58 Python tests and JavaScript locale and rendering checks passed. Both HTTP
servers were retested. The workflow passed local actionlint validation, and
pip-audit reported no known vulnerabilities in the production dependencies at
the time. The hosted workflow was still pending.

## 8 November 2026

- The installer upgraded the standalone WSL installation at
  `/opt/proxypermit-integration`. Windows could open the console on loopback
  port 8098. The unauthenticated configuration API returned 401. Real proxy
  traffic returned 403 before adding a test rule and 200 afterward. The
  original rules were restored.
- A two-node lab with hand-configured network namespaces verified console
  access and proxy traffic on both nodes. After Tinyproxy stopped on node A,
  node B took over and served a new request through the virtual IP after 2.1 s.
- A separate lab checked both installer-generated Keepalived configurations
  with `--config-test`. With the generated health check, a request through the
  same virtual IP returned 200 after 3.2 s when Tinyproxy stopped on node A.

The failover tests shared one WSL host. Outstanding checks include Ubuntu
24.04, upgrade from a production legacy cluster, Nginx/TLS client access,
and a full HA install and network-partition test on separate Linux hosts.

## Reproduce the checks

```bash
python scripts/dev/test.py
python scripts/dev/check_server.py --backend gunicorn
python scripts/dev/check_server.py --backend granian
```

The HA lab requires a Linux test host with Tinyproxy, Keepalived, iproute2, and
the dependencies in `requirements/granian.txt`. Start it from the repository
root with the virtual environment in `.venv`.

```bash
sudo unshare --mount --propagation private .venv/bin/python scripts/dev/ha_lab.py --hold
```

Two isolated network namespaces use a dedicated bridge. The consoles listen on
loopback ports 8101 and 8102 and the proxy on port 8130. The startup test covers
2FA sign-in, replay rejection, and new HTTP traffic after failover. The lab
prints paths to root-only credential files. The management consoles use demo
mode to avoid restarting host services. Tinyproxy and Keepalived run as real
processes. The lab does not exercise automatic policy synchronization.

## 11 November 2026: managed HA and log retention

Two disposable Ubuntu 24.04 VirtualBox guests were converted from standalone
through reviewed GUI plans and a one-time pairing code. Both retained UFW
incoming/outgoing deny defaults. Published filters and shared Tinyproxy settings
arrived automatically on B. Actual proxy requests passed through the VIP on A
and after failover to B. Automatic failure recovery and manual standalone
recovery were exercised. These VMs share one physical host; this does not prove
physical host-loss recovery, fencing, or partition safety.

The new audit and journal retention services ran on a real guest. Synthetic
31-day records were removed from both active and rotated audit files, while
2-day records remained searchable through a UTC date range. The operational
and Tinyproxy journal namespaces were readable through the authenticated API.
Vacuuming those namespaces preserved a sentinel in the default host journal.
Both namespace budgets and successful maintenance service results were checked.

Regression tests cover parallel audit writers/rotation, age and size cleanup,
archive reads across rotation, anchored pagination, malformed records,
IPv4/IPv6 and credential redaction, private-log path restrictions, access
control, and public notices. Playwright covers NL/EN, both themes, mobile logs,
date-filter requests, history/live transitions, and destructive-button contrast.
The deployment organization must still validate its own privacy notice,
external logs, backup/export retention, network plan, and workload.
