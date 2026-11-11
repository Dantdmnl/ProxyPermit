# Install ProxyPermit

[Nederlands](../nl/installatie.md) · [Documentation](README.md)

## Choose a path

- [Install a standalone node](#install-on-debian-or-ubuntu)
- [Set up a two-node HA cluster](#new-two-node-ha-cluster)
- [Configure administrator accounts and 2FA](#administrator-accounts-and-optional-2fa)
- [Upgrade, migrate, or recover](#upgrade-migration-and-recovery)
- [Remove the management console](#remove-the-management-console)

## Install on Debian or Ubuntu

### Run the installer

Use Debian or Ubuntu with systemd and Python 3.10 or later. Run the installer
from the repository root.

For a guided installation, start the wizard.

```bash
sudo bash install.sh
```

To review the default single-node plan separately, run `--plan` and inspect
its output before running `--apply`.

```bash
bash install.sh --plan
sudo bash install.sh --apply
```

A fresh install asks for an administrator password, installs system packages,
builds a release in a dedicated Python virtual environment, and runs tests.
Standalone uses a dedicated service account and a narrow sudoers rule for
restarting Tinyproxy. Keepalived and the sync timer are omitted in this mode.

### Open the console

A fresh standalone install binds both services to loopback. Open an SSH tunnel
and browse to `http://127.0.0.1:8080`.

```bash
ssh -L 8080:127.0.0.1:8080 user@server
```

Set Tinyproxy's `Listen` and `Allow`/`Deny` directives for LAN clients.
For shared web access, see the TLS reverse-proxy examples in `deploy/nginx/`.
Enable `PROXY_FIX_ENABLED=true` only behind a trusted reverse proxy. The
installer preserves an existing administrator-managed Tinyproxy configuration.

For unattended installation, pass `--admin-password-file /path/to/file` and
restrict access to the file. The default release directory is
`/opt/proxypermit/releases/`, with `/opt/proxypermit/current` pointing to the
active release.

### HTTPS and HSTS

Over HTTPS, ProxyPermit sends HSTS for the current host by default. Enable
`HSTS_INCLUDE_SUBDOMAINS=true` only if every subdomain supports HTTPS. Behind
a reverse proxy, accept the HTTPS indicator only from a trusted proxy and
restrict access to the application listener to that proxy. The supplied
Nginx configuration hides the version; removing the generic server name
requires a separate header filter.

## New two-node HA cluster

### Join existing standalone nodes through the web interface

Install or upgrade both hosts as standalone nodes first. Assign static
IPv4 addresses on the HA network. UFW must already be active with incoming
traffic denied by default and working management access. Conversion preserves
existing rules and adds narrow client, VRRP, and internal TLS rules. With
outgoing traffic denied, the added rules permit B’s TLS connection to A
and a TCP listener check through the virtual IP. Proxy traffic
remains subject to the existing outgoing policy.

1. On A, open **Settings → High availability → Set up HA**. Choose
   **Create a new HA cluster** and select the detected HA network. The console
   fills the interface, local address, client subnet, and a virtual-IP suggestion.
   Enter the second node’s static address. Review the client subnet and reserve
   the VIP outside DHCP; a suggestion does not allocate an address.
2. Review the plan and UFW rules. Confirm with your current password and,
   when enabled, your authenticator code. The management console restarts.
3. Sign in again on A and choose **Add node**. The code expires in ten minutes.
4. On B choose **Set up HA → Join using a pairing code**. Paste the code and
   select B’s own HA interface. Review and confirm the conversion.
5. Sign in again on B. Verify matching policy revisions and test proxy traffic
   through the virtual IP.

The helper takes a recovery backup before changing configuration. On failure
it restores the previous files and service state. Installed packages remain.
The backup name appears in progress and the proxypermit-ha-setup.service
journal. Changes to configuration or filters after review invalidate the plan.
When package repositories are blocked, Keepalived, UFW, OpenSSL, and iputils-arping must
already be available. General internet access is not opened.
If service startup is uncertain, the pending request remains locked against
retries. Check `sudo journalctl -u proxypermit-ha-setup.service` and
`sudo systemctl status proxypermit-ha-setup.service` first. Only after the
service has stopped and recovery is verified may an administrator remove a
leftover `/var/lib/proxypermit/ha-setup-request.json` and review a fresh plan.
Treat pairing codes as passwords; do not place them in tickets or logs.
Network discovery reads local interfaces, routes, and known neighbors without
scanning the subnet. Before creating A, the helper uses ARP duplicate-address
detection. A silent host or a later DHCP allocation can still cause a conflict;
reserve the address in IPAM/DHCP. Joining B selects its interface automatically
when exactly one detected address matches the pairing code.

### Install fresh hosts using the installer

Under **Settings → High availability (HA)**, validate the addresses, network
interface, and VRRP ID in advance. The console gives you a plan command for a
fresh host. Run the installation on both hosts using the steps below. On an
existing node, use this screen to validate settings in advance.

On a fresh HA installation, the installer adds three UFW rules. The comments
start with **ProxyPermit HA IN** or **ProxyPermit HA OUT**.

| Direction | Traffic |
| --- | --- |
| IN | TCP 3128 from the selected client network to the virtual IP |
| IN | VRRP from the peer address to the local node address |
| OUT | VRRP from the local node address to the peer address |

The installer preserves the existing UFW activation state and default policies.
If UFW is inactive, the rules are saved for when you enable it. The recovery
backup includes the original UFW rule files. Automatic recovery stops if those
files have changed since installation.

### Retire an HA node

During a maintenance window, verify that the other node holds the virtual IP
and that clients can proxy through it. On the standby node, open
**Settings → High availability (HA) → HA maintenance → Retire this HA node**. Confirm the
handover, enter your password and authenticator code if enabled, then select
**Retire this HA node**.

The host checks that the virtual IP is remote and TCP 3128 is reachable through
it, including after stopping the local services. Retirement removes the
recorded UFW rules for this node. On failure, the installer restores the
firewall files and previous service state. The management console remains
available. Find the backup name and error details in the service journal:

```bash
sudo journalctl -u proxypermit-ha-retire.service -n 50 --no-pager
```

From the host, use `sudo bash install.sh --retire-ha --plan`, followed by
`sudo bash install.sh --retire-ha --apply`. Pass the original `--install-dir`
if it differs from the default. Retirement requires a managed node with a
record of the installed UFW rules. Older deployments require a separate
migration.

Use two clean Debian or Ubuntu hosts with systemd, static IPv4 addresses on the
same network, and an unused virtual IP address. Allow unicast VRRP (IP protocol
112) between the hosts and TCP 3128 from the client network. Choose a VRRP ID
that is unused on that network.

In the wizard, select **Two-node HA**. Review and confirm the summary.
The menu supports arrow keys in compatible terminals and numbered input in
basic terminals. The confirmation defaults to stopping.

Run the following on both hosts. Set `node=b` on the second host and use each
host's interface name and your own addresses.

```bash
node=a
iface=ens18
cluster=(
  --mode ha --ha-node "$node" --ha-interface "$iface"
  --ha-node-a 192.0.2.11 --ha-node-b 192.0.2.12
  --ha-vip 192.0.2.10/24 --ha-client-cidr 192.0.2.0/24 --ha-vrid 51
)
bash install.sh "${cluster[@]}"
sudo bash install.sh "${cluster[@]}" --apply
```

Clients use the virtual IP on port 3128. Keepalived checks the local Tinyproxy
port and gives node A priority. Use a separate SSH tunnel to each management
console. Both nodes start with an empty allowlist. Pair them once through
**Settings → High availability → Add node**. Publish the required policy on A
and confirm that B has applied the same revision before putting the VIP into use.

Upgrades preserve existing Keepalived and sync settings. Topology changes are
rejected during an upgrade. For existing clusters with filter synchronization,
use peer names that match their certificates. Set `SYNC_SCHEME=https`,
`SYNC_VERIFY_CERT=true`, and `SYNC_PORT` if required. Restrict ProxyFix to the
trusted reverse proxy if it terminates TLS. `SYNC_ALLOW_INSECURE_TRANSPORT=true`
is a temporary compatibility option for older deployments.

## Administrator accounts and optional 2FA

A fresh install creates a local administrator account. In **Settings**, add or
remove accounts, change your password, or set up 2FA for your account using an
authenticator app. Every local account has administrator privileges. Account
changes require your password and a fresh authenticator code if 2FA is enabled.
Use HTTPS for remote management. Loopback access can use HTTP.

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python scripts/tools/accounts.py set alice --totp
sudo .venv/bin/python scripts/tools/accounts.py set bob
sudo .venv/bin/python scripts/tools/accounts.py list
```

If an administrator loses access to their authenticator, a server administrator
can issue a new secret with `set USER --totp` or disable 2FA with
`set USER --no-totp`. An account change invalidates that account's existing
sessions. To switch an existing deployment from a single login or RADIUS to
local accounts, create the accounts first, set
`AUTH_ACCOUNTS_FILE=/var/lib/proxypermit/accounts.json`, and restart ProxyPermit.

On HA deployments, enroll accounts and authenticators on each node and sign in
to each node separately. The local JSON audit log records the actor and node.
Share an authenticator secret only with the account owner.

## Upgrade, migration, and recovery

### Upgrade a managed installation

For an installer-managed deployment, obtain the release you want and run the
upgrade from that source directory. The installer uses the source in that
directory. For a custom installation path, pass the same `--install-dir` to
each command.

```bash
sudo bash install.sh --upgrade --plan
sudo bash install.sh --upgrade --apply
```

The installer builds and tests the release before switching, then probes the
management console.

### Migrate an older installation

Use this route for an installation without an installation marker.

```bash
bash install.sh --plan
sudo bash install.sh --apply
```

The installer looks for `/etc/proxypermit/.env` first, then checks the
installation and source directories. It preserves existing settings, password
hashes, secrets, filter rules, Tinyproxy configuration, and Nginx/Unix-socket or
TLS bindings. The migration adds explicit settings to the existing `.env`.

### Restore from a backup

Review the recovery plan first. Use the backup name printed by the installer.

```bash
sudo bash install.sh --rollback BACKUP_NAME --plan
sudo bash install.sh --rollback BACKUP_NAME --apply
```

Backups and their recovery manifests are under `/opt/proxypermit/backups/`.
If installation fails, the installer restores tracked files and the previous
release. Installed packages, system accounts, and empty directories can remain.
After a power interruption, recover manually using the manifest. Recovery
backups contain secrets and are restricted to root.

## Remove the management console

Run the uninstaller from a source directory outside the active installation
path. The plan lists the paths to remove and the data kept on the host. The
installer stops and disables the console and removes the active release link,
unit, and installation marker. It also removes the sudoers rule if it matches
the installer default exactly.

```bash
sudo bash install.sh --uninstall --plan
sudo bash install.sh --uninstall --apply
```

Use the printed backup name to [restore the console](#restore-from-a-backup). Configuration, accounts,
logs, releases, and backups are retained. Tinyproxy, Keepalived, the service
account, and system packages stay on the host. For an existing HA deployment
with synchronization, schedule maintenance first because synchronization can
depend on this node's management API.

## Save and activate configuration

A saved draft is separate from the files Tinyproxy reads. Publishing creates
a restore point, applies configuration and pending native filter changes together,
and restarts Tinyproxy. If activation fails, ProxyPermit restores the previous
files and restarts Tinyproxy again. If recovery also fails, check the service on
the host. A restart can interrupt connections.

Concurrent edits result in a conflict. The domain editor is locked while a
configuration draft is pending. Coordinate maintenance on each node when using
older editing routes or HA synchronization.

## Choose a web server

Gunicorn is the default. Granian is an optional Rust HTTP server for the same
WSGI application.

```bash
bash install.sh --server granian --plan
sudo bash install.sh --server granian --apply
```

For local use, install `requirements/granian.txt` and run
`python -m app.runtime.server --backend granian`. Add `--check` to print the
command. `APP_BIND` and `APP_WORKERS` set the binding and worker count.
Upgrades keep the existing `APP_SERVER` unless you explicitly choose another.
Gunicorn supports direct TLS in this deployment. Use a TLS reverse proxy with
Granian. See [architecture and HA](architecture.md).
