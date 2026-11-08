# ProxyPermit

ProxyPermit is a web console for managing Tinyproxy on Linux. It covers client
access, destination filtering, upstream and reverse-proxy settings, configuration
drafts, and live logs. Administrators can use individual accounts with optional
two-factor authentication.

Run it on a single node or use two nodes with a shared virtual IP managed by
Keepalived. In a new HA deployment, each node has its own console and proxy
policy. See the installation guide for migration from an existing HA deployment.

| Documentation | English | Nederlands |
| --- | --- | --- |
| Start here / Begin hier | [Documentation](docs/en/README.md) | [Documentatie](docs/README.md) |
| Install and upgrade / Installatie en upgrades | [Installation guide](docs/en/installation.md) | [Installatiehandleiding](docs/installatie.md) |
| Daily operations / Dagelijks beheer | [Administrator guide](docs/en/administration.md) | [Beheerdershandleiding](docs/beheer.md) |

## Quick start

On Debian or Ubuntu with systemd and Python 3.10 or later, run the installer
from the repository root.

```bash
bash install.sh --plan
sudo bash install.sh
```

The wizard lets you choose a standalone node or a two-node HA deployment.
The management console listens on loopback by default. See the installation
guide for an SSH tunnel, HTTPS access, and HA prerequisites.

## Development

Run `python -m pip install -r requirements/dev.txt` in a Python 3.11+ virtual
environment, then run `python scripts/check.py`. The [verification report](docs/en/verification.md)
lists the Linux and HA scenarios tested so far.
