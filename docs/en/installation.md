# Install ProxyPermit

[Nederlands](../nl/installatie.md) · [Documentation](README.md)

**For:** the system administrator installing or maintaining the server. To use
an existing console, start with [First steps](getting-started.md).

Before you start, have a supported Debian/Ubuntu host, root or sudo access and
an independent way back into the server. Obtain the source for the version you
intend to install and run the commands from the directory containing `install.sh`.
The examples use documentation addresses; replace them with your network values.
See [requirements and tested platforms](requirements.md) before choosing a host.

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

Start the wizard in a Linux console or SSH terminal. When you run it as a
regular user, it requests sudo access after you confirm the plan and reuses
the settings you entered.

```bash
bash install.sh
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

Press Enter to accept a displayed default. The wizard checks the local console
port and offers a nearby port if needed. You can edit settings in the review
screen. Gunicorn is the default. Both Gunicorn and Granian support direct HTTPS.

### Protect management access

The wizard asks for the trusted management IP/network and the real host-side
SSH and web ports. Check these values in the review. Enter the source that the
host actually sees; NAT and reverse proxies can change it. Do not enter a broad
network just to make installation easier. A local installer cannot safely infer
a remote administrator's network.

For automation or an existing installation, use the explicit
`--management-cidr`, `--management-ssh-port` and `--management-web-port` options.
See the [anti-lockout profile](administration.md#anti-lockout-profile) for an
example, recovery behavior and restrictions.

On a fresh install, choose **Enable UFW after adding ProxyPermit rules** to
activate UFW once the management and proxy rules are in place. Its default
inbound policy must be deny or reject. When the wizard detects your SSH
connection, the trusted network and SSH port must cover it. Keep an independent
console available and review existing firewall rules and other inbound services.

For automation, add `--enable-ufw` and an explicit `--management-cidr`.
Specify the host SSH and web ports when they differ from the defaults. The
default firewall choice and upgrades preserve the activation state.

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

Authentication is enabled by default. Use `/connection` for a public liveness
probe. The detailed `/health` response requires a signed-in account. Extra
browser origins configured with `MANAGER_ALLOWED_ORIGINS` apply only to legacy
session transfer.

For a fresh interactive installation, the wizard suggests **HTTPS** with a
local CA on `127.0.0.1:8444`. If you choose **HTTP**, it suggests port 8080.
You can change the port or switch
protocols from **Edit management and firewall settings** before applying the
plan. Keep the listener on loopback and use a tunnel or a reverse proxy for
remote access. Port 8443 is reserved for HA synchronization.
Non-interactive installs use HTTP unless you set `--manager-tls`.

With HTTPS, choose one of these certificate sources.

- **Local CA** creates a self-signed trust root and a server certificate that
  renews automatically. Add any DNS names or IP addresses clients will use.
  Verify the SHA-256 fingerprint printed by the installer and trust
  `/etc/proxypermit/management-tls/ca.pem` on each client.
- **Own certificate** imports an absolute path to a root-owned PEM chain with
  the server certificate first. The matching, unencrypted PEM key must be root
  only (`0600`). Connect using a name on the certificate and manage renewal of
  the installed copies separately.

For automation, preview with `bash install.sh --manager-tls local-ca --plan`
or use `--manager-tls custom` with `--manager-cert` and `--manager-key`.
Upgrades preserve the current listener. See [Management HTTPS](management-https.md)
to enable HTTPS on an existing installation.

Over HTTPS, ProxyPermit sends HSTS for the current host by default. The local
CA option starts with HSTS off while you establish client trust. Enable
`HSTS_INCLUDE_SUBDOMAINS=true` only if every subdomain supports HTTPS. Behind
a reverse proxy, accept the HTTPS indicator only from a trusted proxy and
restrict access to the application listener to that proxy. The supplied
Nginx configuration hides the version; removing the generic server name
requires a separate header filter.

## New two-node HA cluster

This workflow configures Keepalived and policy synchronization. It does not
automatically configure quorum, a witness or fencing. Read
[HA protection and its limits](high-availability.md#understand-failover-protection)
before production deployment.

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

Use this route to provision two fresh hosts directly with the installer. For
existing standalone nodes, use the web-interface procedure above instead. The
[HA guide](high-availability.md) explains the addresses and the role of each node.

On a fresh HA installation, the installer adds three UFW rules. The comments
start with **ProxyPermit HA IN** or **ProxyPermit HA OUT**.

| Direction | Traffic |
| --- | --- |
| IN | TCP 3128 from the selected client network to the virtual IP |
| IN | VRRP from the peer address to the local node address |
| OUT | VRRP from the local node address to the peer address |

The installer preserves UFW's default policies. With the default wizard
choice, an inactive firewall keeps the new rules for later use. The recovery
backup records the original rules and any UFW activation by the installer.
Automatic rollback checks for later changes to those files.

Use two clean Debian or Ubuntu hosts with systemd, static IPv4 addresses on the
same network, and an unused virtual IP address. Allow unicast VRRP (IP protocol
112) between the hosts and TCP 3128 from the client network. Choose a VRRP ID
that is unused on that network.

In the wizard, select **Two-node HA**. Select a detected static IPv4 address to
prefill the local interface and address, or enter both manually. Specify the
peer address, the reserved virtual IP and the client network. The wizard
checks the values as you enter them. Review and confirm the summary.
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
bash install.sh "${cluster[@]}" --plan
sudo bash install.sh "${cluster[@]}" --apply
```

Clients use the virtual IP on port 3128. The managed Keepalived helper checks whether local Tinyproxy answers HTTP
and gives node A priority. Use a separate SSH tunnel to each management
console. Both nodes start with an empty allowlist. Pair them once through
**Settings → High availability → Add node**. Publish the required policy on A
and confirm that B has applied the same revision before putting the VIP into use.

Upgrades preserve existing Keepalived and sync settings. Topology changes are
rejected during an upgrade. For existing clusters with filter synchronization,
use peer names that match their certificates. Set `SYNC_SCHEME=https`,
`SYNC_VERIFY_CERT=true`, and `SYNC_PORT` if required. Restrict ProxyFix to the
trusted reverse proxy if it terminates TLS. `SYNC_ALLOW_INSECURE_TRANSPORT=true`
is a temporary compatibility option for older deployments.

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

## Administrator accounts and optional 2FA

A fresh install creates a local administrator account. In **Settings**, add or
remove accounts, change your password, or set up 2FA for your account using an
authenticator app. The first account is a system administrator; new accounts can have a [restricted role](roles.md). Account
changes require your password and a fresh authenticator code if 2FA is enabled.
Use HTTPS for remote management. Loopback access can use HTTP.

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python scripts/tools/accounts.py set alice --role admin --totp
sudo .venv/bin/python scripts/tools/accounts.py set bob --role manager
sudo .venv/bin/python scripts/tools/accounts.py list
```

The example explicitly creates an administrator and a proxy manager. Omit
`--totp` if you do not want to enroll optional 2FA during this step.

If an administrator loses access to their authenticator, a server administrator
can issue a new secret with `set USER --totp` or disable 2FA with
`set USER --no-totp`. An account change invalidates that account's existing
sessions. To switch an existing deployment from a single login or RADIUS to
local accounts, create the accounts first and verify that `list` shows at least
one `admin`. New CLI accounts default to monitor unless `--role` is supplied. Then set
`AUTH_ACCOUNTS_FILE=/var/lib/proxypermit/accounts.json`, and restart ProxyPermit.

On HA deployments, enroll accounts and authenticators on each node and sign in
to each node separately. The local JSON audit log records the actor and node.
Share an authenticator secret only with the account owner.

## Upgrade, migration, and recovery

### Upgrade a managed installation

For an OS-prepared quorum/fencing cluster, use
[protected-cluster maintenance](administration.md#maintenance-on-an-os-protected-cluster)
instead of the ordinary upgrade commands below. Independent setup, rollback,
retirement and uninstallation are blocked on those protected nodes. Use
the protected maintenance workflow to roll back a completed application upgrade.
After OS detachment, use the [protected node removal procedure](administration.md#remove-a-detached-protected-node).

When upgrading to v1.3.0, upgrade **both** HA nodes before rotating their peer
key or internal certificate. Existing HA remains Keepalived-based unless you
separately provision the experimental protected cluster. Reload remains opt-in;
existing HTTP management listeners are preserved.

For an installer-managed deployment, obtain the release you want and run the
upgrade from that source directory. The installer uses the source in that
directory. For a custom installation path, pass the same `--install-dir` to
each command.

Before applying an upgrade, coordinate any saved configuration drafts with their
owner. Publish or discard them deliberately; anti-lockout configuration refuses
to overwrite a pending draft. Keep a recovery console and record the backup name.
Existing passwords, 2FA and role-less administrator access are preserved. After
upgrading, review [roles](roles.md), the management-source profile and HA policy
status on both nodes. A monitor cannot export policy; use a proxy manager or
system administrator for a pre-upgrade export.

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

To permanently remove local ProxyPermit data as well, preview then apply a
purge from a separate source checkout.

```bash
sudo bash install.sh --uninstall --purge --plan
sudo bash install.sh --uninstall --purge --apply
```

Purge removes local accounts, manager settings, certificates, dedicated logs,
releases and recovery backups. Export anything you need before applying it.
Tinyproxy can briefly restart to return to host logging. SSH settings, pinned
management access, UFW configuration, Tinyproxy configuration and system
packages are retained. Retire an HA node first. For protected HA, use
`--detached-ha` after completing OS detachment and retirement.

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
Both backends support direct TLS and can run behind a TLS reverse proxy. See
[architecture and HA](architecture.md).
