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
2. Create one account per administrator. All local accounts have administrator
   privileges. Optional 2FA is available under **Settings**. Verify sign-in and
   identify who can restore access from the server if an authenticator is lost.
3. Restrict Tinyproxy to the intended client networks. From an authorized client,
   verify both an allowed and a blocked destination. Also check that an
   unauthorized client cannot forward traffic.
4. Publish a small change, retest client traffic, and find the actor and node in
   the audit log under **Live logs**. Repeat on both nodes for a new HA cluster.

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

On a new HA cluster, publish the same policy on both nodes. Start with the node
that does not hold the virtual IP and verify it before changing the active node.

## Troubleshoot a deployment

Open **Live logs** and filter by source and level. Type to filter loaded
entries or select **Search server** to include older entries. Expand an audit
entry to see details about the change.

Check the services and journal on the host.

```bash
sudo systemctl status proxypermit.service tinyproxy.service --no-pager
sudo journalctl -u proxypermit.service -u tinyproxy.service -n 100 --no-pager
# On HA nodes
sudo systemctl status keepalived.service --no-pager
ip -br -4 address
sudo journalctl -u keepalived.service -n 100 --no-pager
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

- Local accounts share one administrator privilege level. Read-only roles and
  centrally managed identity are outside the current local account model.
- Audit logs are local. Define collection and retention for your organization.
- The WSL lab verified virtual IP failover. Validate installation, host loss,
  network partitions, and recovery on the separate hosts you intend to use.
  [Verification report](verification.md)
