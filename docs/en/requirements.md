# Requirements and tested platforms

[Nederlands](../nl/vereisten.md) · [Installation](installation.md) · [Verification](verification.md)

## What you need

| Deployment | Host and network requirements |
| --- | --- |
| Standalone | One Debian or Ubuntu host with systemd, Bash, Python 3.10 or later, and root or sudo access. Provide access to the OS package repositories and to the Python package source used by the installer. |
| Managed two-node HA | Two Linux hosts with static IPv4 addresses, a free virtual IPv4 address (VIP) outside DHCP, direct SSH and management access to both nodes, and a client subnet that can reach the VIP. Use the [HA network checklist](high-availability.md). |
| Protected HA preview | The managed pair, synchronized time, an independent witness and a fencing path that still works when a node is unreachable. Use [the protected HA guide](high-availability.md#understand-failover-protection) to check the network and failure domains before setup. |

## What is installed

| Component | Dependencies and purpose |
| --- | --- |
| OS packages | The normal installer uses APT for `python3-venv`, `tinyproxy`, `sudo`, `logrotate`, `ufw` and `acl`. Tinyproxy handles proxy traffic. Systemd manages services and journals. The installer adds `keepalived` when creating a managed HA pair. |
| Management console | The installer creates a Python virtual environment. Flask and Werkzeug provide the web application, Gunicorn is the default server, and Granian is an optional server. Requests and python-dotenv handle HTTP and configuration. PyOTP and Segno support optional authenticator-based 2FA, and pyrad supports optional RADIUS. The exact pins are in [the release requirements files](https://github.com/Dantdmnl/ProxyPermit/tree/main/requirements). |
| Protected HA nodes | The preparation tool installs `pacemaker`, `pacemaker-cli-utils`, `corosync`, `corosync-qdevice`, `pcs` and `libnss3-tools`. The fencing agent and its runtime depend on the platform. The native Proxmox workflow supplies a pinned ClusterLabs-derived agent which uses the OS fencing Python library and PycURL. |
| Witness | `corosync-qnetd` and `libnss3-tools` on its own Linux host. Use mutual TLS between qdevice and QNetd. The witness needs no proxy, web console or fencing token. |
| Development and documentation | `requirements/dev.txt` lists development dependencies. Browser tests use Node.js, Playwright and Chromium. The [docs build](documentation-site.md) uses Python 3.11 or later, `requirements/docs.txt`, Node.js 24 and the npm lockfile in `scripts/docs`. These tools run on development systems and CI. |

Optional features have narrower compatibility requirements. In particular, the
[connection-preserving filter reload](administration.md#optional-filter-reload)
has been verified with Tinyproxy **1.11.1 and 1.11.3**.

## Network paths for managed HA

| Source and destination | Traffic |
| --- | --- |
| Allowed clients → VIP | TCP 3128 for the managed Tinyproxy listener. |
| A ↔ B | VRRP for ordinary HA. For the protected profile, allow UDP 5405 for Corosync. |
| B → A | TCP 8443 for the managed policy synchronization service. |
| A and B → witness | TCP 5403 for QNetd with mutual TLS. |
| A and B → Proxmox API | TCP 8006 for the native Proxmox fencing adapter, with certificate verification and peer-only credentials. |
| Administrators → each node | Direct SSH and the configured console endpoint. Use each node’s own address during maintenance. |

Review the installer’s generated firewall plan for the exact interfaces and
addresses. Ensure both the witness and the fencing path survive the failures
the cluster is expected to handle.

## What has been tested

| Environment | Scope of verification |
| --- | --- |
| Linux CI and Windows CI | The configured Python test matrix is Linux 3.10, 3.12 and 3.14 and Windows 3.14. The root suite runs in an Ubuntu 24.04 container. Windows covers application and browser development. Installation targets Linux. |
| Ubuntu WSL 2 and VirtualBox | Standalone integration on Ubuntu 26.04.1 in WSL 2, and installation, pairing, synchronization and ordinary HA on Ubuntu 24.04 VirtualBox guests. |
| Proxmox VE with Ubuntu 24.04 guests | Standalone and protected HA lab trials, including interrupted migration, guest fencing, partition recovery and rolling release rollback. The nodes and witness shared a hypervisor in these trials. |
| KVM/libvirt with Ubuntu 24.04 guests | An OS-managed protected HA lab using `fence_xvm` and `fence_virtd`. The lab verified fencing, VIP ownership and recovery after a temporary fencer outage. The administrator provisions and maintains the cluster. |
| VMware vSphere and physical servers with IPMIv2 | ProxyPermit checks supported fencing evidence from OS-managed clusters. Automated tests cover unsafe configurations. Live fencing and full lifecycle trials for these platforms remain open. |

The [verification report](verification.md) records dates, tested scenarios and
the limits of each test. The [roadmap](planned-work.md) tracks the remaining
release work. For an installed older version, use the documentation bundled
with that release.
