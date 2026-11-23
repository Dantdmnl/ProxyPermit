# Administrator guide

[Nederlands](../nl/beheer.md) · [Documentation](README.md)

Use this guide to commission ProxyPermit and hand it over to operations. The
management console and the Tinyproxy listener have separate network and access
requirements.

## Choose a deployment

| Deployment | Management | Client traffic |
| --- | --- | --- |
| Standalone | One console and one policy | One Tinyproxy address |
| New HA cluster | A console and local policy on each node | One virtual IPv4 address shared through Keepalived |
| Existing cluster with synchronization | Existing node settings and separate filter synchronization | Existing virtual address |

Start on an isolated Debian or Ubuntu host with systemd. For HA, reserve two
static node addresses and an unused virtual IP. Follow the
[installation guide](installation.md). Record the release, ports, client network,
management address, and backup name in the change record.

## Commission the deployment

1. Review the installer plan. Use an SSH tunnel or HTTPS through a reverse
   proxy to reach the console. Restrict access to the management network, use
   a Secure session cookie with HTTPS, and trust forwarded headers only from
   your reverse proxy.
2. Create one account per administrator. Choose the appropriate [role](roles.md) for each account. Optional 2FA is available under **Settings**. Verify sign-in and
   identify who can restore access from the server if an authenticator is lost.
3. Restrict Tinyproxy to the intended client networks. From an authorized client,
   verify both an allowed and a blocked destination. Also check that an
   unauthorized client cannot forward traffic.
4. Publish a small change, retest client traffic, and find the actor and node in
   the audit log under **Live logs**. On paired HA nodes, publish on A and verify
   the matching applied revision on B.

Use two internal endpoints with known HTTP responses. Replace the examples.

```bash
proxy=http://192.0.2.10:3128
curl --noproxy '' --proxy "$proxy" --max-time 10 -i http://allowed.example/
curl --noproxy '' --proxy "$proxy" --max-time 10 -i http://blocked.example/
```

With an allowlist, expect the normal response from the first destination and
HTTP 403 from the second. Test HTTPS through CONNECT if clients use it.
Verify actual proxy traffic in addition to checking systemd and console health.

## Change proxy policy

1. Record which destinations must be reachable after the change.
2. Open **Proxy configuration**. Select **Validate draft**, review errors and
   warnings, then select **Save draft**.
3. Select **Publish and activate** during the maintenance window. Review the
   confirmation: the default restart may interrupt existing connections. A
   verified, enabled reload path preserves them for compatible filter changes.
4. Retest client traffic and verify the change in **Live logs**.

On paired managed HA nodes, publish shared policy on A. B retrieves and
activates it automatically; verify matching revisions before testing failover.
Unpaired nodes retain independent policy. Keep account and software maintenance
separate from policy publication.

## Optional filter reload

By default, publishing restarts Tinyproxy and may interrupt connections.
v1.3.0 adds an opt-in reload path for compatible filter changes on **Tinyproxy
1.11.1 and 1.11.3** with managed journal logging. Other changes still require a
restart; a failed reload never silently falls back to one.

To opt in, have the OS administrator set `TINYPROXY_RELOAD_ENABLED=true` in
`/etc/proxypermit/.env`, then run the supported installer upgrade from the new
release. On a protected cluster, use the cluster-maintenance workflow below.
The installer grants Tinyproxy read-only ACL access to its native configuration
and filter file without adding it to the management group. Existing custom ACL
masks that block reading require OS review. Recheck permissions if you change
Tinyproxy's operating-system account.

Verify allowed/blocked destinations, a continuous HTTP transfer and a CONNECT
tunnel on your hosts before relying on this option. Existing tunnels remain
open; new rules do not retroactively close them. Extended power-loss and storage
acceptance remains on the [roadmap](planned-work.md).

## Troubleshoot a deployment

See [HA protection and its limits](high-availability.md#understand-failover-protection)
if the console says quorum and fencing are not configured. This does not indicate
a policy synchronization failure.

Open **Live logs** and filter by source and level. Type to filter loaded
entries or select **Search server** to include older entries. Expand an audit
entry to see details about the change. Use **Date range** to search retained
audit archives and journal history. Review incomplete-result notices. See
[logging, privacy and retention](logging.md) for storage limits and exports.

Check the services and journal on the host.

```bash
sudo systemctl status proxypermit.service tinyproxy.service --no-pager
sudo journalctl --namespace=proxypermit -u proxypermit.service -n 100 --no-pager
sudo journalctl --namespace=proxypermit-traffic -u tinyproxy.service -n 100 --no-pager
# On HA nodes
sudo systemctl status keepalived.service --no-pager
ip -br -4 address
sudo journalctl --namespace=proxypermit -u keepalived.service -n 100 --no-pager
```

| Symptom | Check |
| --- | --- |
| Sign-in is temporarily blocked | Wait for the lockout to expire. Changing the language or opening a new tab leaves the server's expiry time in place. |
| Console is unreachable | Check the management unit, listening port, and SSH tunnel or TLS reverse proxy. |
| Unexpected HTTP 403 from the proxy | Check client access rules, filter mode, destination rule, and whether the draft was activated. |
| Failover fails | Check which node holds the virtual IP, Keepalived logs, and VRRP traffic. Retest client traffic through the virtual IP. |
| Nodes have different policies | Compare active configuration and filter files. On clusters with legacy sync, also check TLS and synchronization logs. |

## Backup, upgrade, and recovery

Back up installation settings, Tinyproxy configuration, filter rules, the account
file, keys, and relevant service state. Verify the paths in
`/etc/proxypermit/.env`. For HA, include Keepalived and node-specific state.
Restrict access to the backups because they can contain password hashes, 2FA
secrets, and sync secrets. Keep a copy off the node and practice recovery in a lab.

Review the [upgrade plan](installation.md#upgrade-a-managed-installation) and
record the current policy. The installer prints the backup name. Before
[restoring a backup](installation.md#restore-from-a-backup), review the affected
files and save any newer policy. Verify sign-in and proxy traffic again after
recovery.

## Current limits

- Three local roles are supported. Central identity and account replication
  are separate from the current local account model.
- Audit logs are local. Define collection and retention for your organization.
- VirtualBox HA and a separate KVM quorum/fencing prototype have been tested.
  The normal HA workflow does not provision that protected cluster. Validate
  installation, host loss, network partitions and recovery on your intended platform.
  [Verification report](verification.md)

## Connection loss

Every loaded page checks whether the management console is reachable. A connection notice distinguishes a browser without a network connection from an unavailable console. It also appears above open dialogs, supports light and dark themes, and checks again every five seconds during an outage. This check does not inspect Tinyproxy or the HA peer.

Your inputs and open dialogs remain on the current page. Navigation and new write requests are held while the console is unavailable. Recovery does not reload the page or repeat actions. If a write response was lost, check the current rules or settings before repeating the action: the server may already have applied it. Existing revision checks still protect concurrent changes.

Keep the tab open to retain unsaved input. This is connection recovery for an already loaded page, not offline administration. Opening or reloading a page still needs the server; credentials and management data are not stored in an offline cache.

## Anti-lockout profile

Pin the actual trusted management source networks and host-side SSH/web ports.
Use the reverse-proxy or relay listener port, not an unrelated public NAT port.
Keep an independent console and a second working management session available.
Replace the documentation address and installation path in this example:

```sh
sudo bash install.sh --upgrade --install-dir /opt/proxypermit \
  --management-cidr 192.0.2.8/32 --management-ssh-port 22 \
  --management-web-port 443 --plan
```

Review that plan and repeat with `--apply`. Repeat CIDR and port arguments when
necessary. Without an existing profile or an explicit network, an installing SSH
peer can supply the initial single-host source; local unattended installations
without either remain visibly unconfigured. Never substitute `0.0.0.0/0` or `::/0`.
The interactive installer asks for the trusted network and actual web port.

The root-owned `/etc/proxypermit/access-safety.json` is readable by the service
account but cannot be rewritten by the GUI. Additive UFW rules use the prefix
`ProxyPermit SAFETY IN - ...` and are inserted before ordinary rules. They are
checked in dry-run first; UFW activation and default policies are preserved.
Existing stateful return-traffic rules handle responses, without a broad new OUT
exception. Configuration, profile and firewall files are included in recovery.

The local Allow block is excluded from shared HA policy and policy-conflict
baselines, while native edit revisions still cover the complete file. A backup
keeps its own pinned management access when A publishes client rules. Restart
and rollback remain separate from draft saves. Ports/listeners and removal of
fixed exceptions are checked on the server, including incoming shared policy.

The GUI cannot change a pinned profile. Installer arguments add protected sources
and ports without removing existing ones. Plan removal of old exceptions through
out-of-band OS maintenance, reviewing the profile, native ACL block and owned UFW rules
together. Test a second management session before closing the original one.
This safeguards utility-managed changes; it cannot stop a root operator from
manually resetting the firewall or deleting the profile.

The local exception precedes shared ACLs because Tinyproxy stops at the first
matching client access rule; this is distinct from upstream routing order.
See the [Tinyproxy ACL implementation](https://github.com/tinyproxy/tinyproxy/blob/master/src/acl.c).

### Why management sources can be identical on several nodes

The profile lists allowed **source** addresses, not each node's own addresses.
In the VirtualBox lab, all three independent NAT connections report the forwarded
host connection as `10.0.2.2`. A `/32` protects that single observed source. This
is why the standalone and HA nodes can correctly show the same value. It does
not imply that their HA addresses or identities are identical. Behind a shared
NAT or reverse proxy, enforce any per-client network restrictions at that entry
point too; the source rule never replaces authentication or HTTPS.

## Renewing the HA peer key

Upgrade both nodes first and wait for successful automatic synchronization.
On A, open **Settings → High availability → Rotate peer key**. Confirm with your
current password and, if enabled for your account, a fresh authenticator code.
B receives the new key automatically through the existing pinned TLS connection.
It saves the candidate before asking A to revoke the old key. The interface shows
the transition and its completion; it never displays either peer key.

The transition expires after ten minutes. If it expires before B commits, both
keys are rejected and the nodes must be paired again. A lost commit response can
be retried with B's saved candidate. Policy and existing proxy connections remain
unchanged during key rotation. Certificate renewal is a separate operation.

If you use the older nginx-based internal TLS example, allow
`/internal/ha/credential-commit` alongside `enroll` and `policy` before rotating.
Keep the peer source-address restriction and all other paths denied. Back up the
configuration, run `sudo nginx -t`, then reload nginx. The updated example is
`deploy/nginx/proxypermit-ha.conf.example`. The managed Gunicorn TLS endpoint
already restricts access to these three machine endpoints. Do not expose the
management interface through this internal listener.

## Renewing the internal HA certificate

This is an OS-administrator operation on A, separate from the certificate used
by the management website. Upgrade both nodes and check successful synchronization
first. The current internal certificate must remain valid for at least eleven
minutes. The following commands use the default installation directory:

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python -m scripts.deploy.ha_certificates plan
sudo .venv/bin/python -m scripts.deploy.ha_certificates stage
sudo .venv/bin/python -m scripts.deploy.ha_certificates activate
```

Allow B to poll after staging. Activation refuses to commit until B has saved
the candidate pin; retry `activate` after the next synchronization if needed.
For an older nginx-based internal listener, add `--tls-service nginx.service` to
`stage`. With a custom installation path, also pass `--install-dir /your/path`.
Management restarts briefly; Tinyproxy and Keepalived are preserved.

Before commitment, `cancel` withdraws the offer without changing the active
certificate. After commitment, recovery must finish the new certificate; never
restore only the old certificate or old pin. An OS recovery unit completes an
interrupted commit at boot. Use `recover` to retry it manually, then `activate`
to obtain B's confirmation. An expired handover or changed peer may require
out-of-band review and pairing again. Verification is never disabled.

Candidate keys, original certificate/key/environment backups and the recovery
plan remain root-private under `/etc/proxypermit/ha-tls/maintenance`. Public
handover metadata is stored separately in
`/etc/proxypermit/ha-certificate-transition.json`; the web service does not need
access to a legacy private TLS directory. Maintenance events include the OS UID
and operation ID in the existing private audit log and follow its retention policy.

## Maintenance on an OS-protected cluster

This workflow is for an OS administrator who has already configured the tested
Pacemaker/Corosync resource, independent TLS witness and fencing. The normal HA
setup wizard does **not** provision these components yet. Keepalived must have
independent boot activation and automatic restarts disabled: Pacemaker owns it.

**Failover protection** in the interface shows recent root-verified cluster
evidence. It distinguishes protection not configured, uncertain state, degraded
protection, a stopped resource and conflicting ownership. Healthy evidence expires
after at most twenty seconds without a successful refresh. A configured fencer
must explicitly map both nodes to distinct targets and be monitored as available.
This does not replace a real fencing test or verify the independence of the
hypervisor, witness and power infrastructure.

For an app upgrade, extract the new release and run its installer on one node:

```bash
sudo bash install.sh --upgrade --cluster-maintenance --plan
sudo bash install.sh --upgrade --cluster-maintenance --apply
```

Use `--install-dir /your/path` for a custom installation. The installer requires
healthy quorum, witness and fencing, reserves maintenance for the whole cluster,
then puts this node in standby. Local changes start only after the peer owns the
protected VIP. OS packages, Keepalived configuration and cluster services are
preserved. After checking the manager and native proxy, it restores the exact
previous standby setting and releases the reservation. Wait for stable cluster
status before updating the second node. Shared policy is still published on A;
moving the VIP does not give B write authority.

To roll back the application release after a completed protected upgrade, use
the current backup name printed by that upgrade. Run the installer that
performed the upgrade from its extracted source directory:

```bash
sudo bash install.sh --rollback BACKUP_NAME --cluster-maintenance --plan
sudo bash install.sh --rollback BACKUP_NAME --cluster-maintenance --apply
```

Review the selected release before applying. The peer takes the VIP first.
The installer rebuilds the previous application release with current
configuration, accounts and filter rules, and keeps current fencing, firewall
rules and the management listener. Check that the previous application
version is compatible with current data before proceeding. This procedure
requires the protected-upgrade receipt written by the installer.

An interrupted or failed upgrade leaves the reservation in place. It has no
automatic expiry, so the other node cannot also enter maintenance. Review the
installer's backup manifest and repair the local installation first. Then use the
installed release to verify recovery:

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python -m scripts.deploy.cluster_maintenance --recover
```

Recovery verifies local manager/proxy health, peer protection and matching
reservation ownership before restoring standby. Maintenance release retries
unconfirmed peer protection for up to 20 seconds before retaining the checkpoint
for recovery. It refuses changed operator
settings or an ambiguous checkpoint. Do not delete
`/etc/proxypermit/cluster-maintenance.json`, clear another node's reservation or
start Keepalived directly to bypass a refusal. Review unresolved cases through OS
cluster administration and out-of-band access. Restoring a full installation
backup still requires OS-managed cluster recovery.

### Remove a detached protected node

Keep direct SSH and management access to both nodes and independent recovery
access to the fencing platform. First arrange the surviving service through
OS cluster administration. Verify the peer is serving the VIP and will keep
quorum for the change. Stop the departing node's cluster participation. Its
`pacemaker.service`, `corosync.service`, `corosync-qdevice.service` and
`keepalived.service` must be inactive and persistently masked. The installer
does not edit the peer's cluster membership, witness or fencing topology.

Use the release source on the detached node. Review the owned firewall rules
and services in the plan before applying:

```bash
sudo bash install.sh --retire-ha --detached-ha --plan
sudo bash install.sh --retire-ha --detached-ha --apply
```

Retirement requires a native checkpoint and an absent local VIP. It verifies
that the peer's proxy and the local console remain reachable and removes only
the recorded HA and native cluster rules. Pinned management rules remain in
place. The local management console remains available after retirement.

To remove that console as well, verify SSH access, then review and apply:

```bash
sudo bash install.sh --uninstall --detached-ha --plan
sudo bash install.sh --uninstall --detached-ha --apply
```

The uninstaller checks retirement and verifies that the native services
remain masked. OS packages, cluster recovery records, policy and backups
remain available for the OS administrator. Follow your cluster procedure to
replace the node or reconfigure the surviving deployment.

The installer also validates private native draft backups. It refuses symlinks,
linked files and legacy drafts with unsuitable ownership or permissions instead
of recursively changing application-writable paths as root. Review those files
through OS administration; do not apply a broad recursive permission command.
