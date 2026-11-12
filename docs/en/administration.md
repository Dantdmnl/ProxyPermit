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
3. Select **Publish and activate** during the maintenance window. Tinyproxy
   restarts and may interrupt existing connections.
4. Retest client traffic and verify the change in **Live logs**.

On paired managed HA nodes, publish shared policy on A. B retrieves and
activates it automatically; verify matching revisions before testing failover.
Unpaired nodes retain independent policy. Keep account and software maintenance
separate from policy publication.

## Troubleshoot a deployment

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
- The WSL lab verified virtual IP failover. Validate installation, host loss,
  network partitions, and recovery on the separate hosts you intend to use.
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
