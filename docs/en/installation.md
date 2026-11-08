# Install ProxyPermit

[Nederlands](../installatie.md) · [Documentation](README.md)

## Install on Debian or Ubuntu

Use systemd and Python 3.10 or later. Run the installer from the repository root.

```bash
bash install.sh                            # Start the interactive wizard
sudo bash install.sh                       # Run the wizard with installation privileges
bash install.sh --plan                     # Print the default plan as JSON
sudo bash install.sh --apply
```

A fresh install asks for an administrator password, installs system packages,
builds a release in a dedicated Python virtual environment, and runs tests.
Standalone uses a dedicated service account and a narrow sudoers rule for
restarting Tinyproxy. Keepalived and the sync timer are omitted in this mode.

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

```bash
bash install.sh --plan
sudo bash install.sh --apply
bash install.sh --rollback BACKUP_NAME
sudo bash install.sh --rollback BACKUP_NAME --apply
```

The installer looks for `/etc/proxypermit/.env` first, then checks the
installation and source directories. It preserves existing settings, password
hashes, secrets, filter rules, Tinyproxy configuration, and Nginx/Unix-socket or
TLS bindings. The migration adds explicit settings to the existing `.env`.

Backups and their recovery manifests are under `/opt/proxypermit/backups/`.
If installation fails, the installer restores tracked files and the previous
release. Installed packages, system accounts, and empty directories can remain.
After a power interruption, recover manually using the manifest. Recovery
backups contain secrets and are restricted to root.

## New two-node HA cluster

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
console. Both nodes start with an empty allowlist. Add the required destinations
and publish the same policy separately on each node before putting it into use.

Upgrades preserve existing Keepalived and sync settings. Topology changes are
rejected during an upgrade. For existing clusters with filter synchronization,
use peer names that match their certificates. Set `SYNC_SCHEME=https`,
`SYNC_VERIFY_CERT=true`, and `SYNC_PORT` if required. Restrict ProxyFix to the
trusted reverse proxy if it terminates TLS. `SYNC_ALLOW_INSECURE_TRANSPORT=true`
is a temporary compatibility option for older deployments.

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
