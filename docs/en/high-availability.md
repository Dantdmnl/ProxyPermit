# Set up high availability

[Nederlands](../nl/hoge-beschikbaarheid.md) Â· [Documentation](README.md)

**For:** a system administrator with two working standalone nodes.
**Result:** clients use one shared proxy address and B automatically receives
policy published on A. Try this in the [VM lab](vm-lab.md) before production.

!!! warning "Quorum and fencing are not configured automatically"
    This workflow sets up Keepalived and policy sync. It does not yet provide
    enforceable split-brain protection during network partitions. Read
    [Understand failover protection](#understand-failover-protection) before production use.

## Understand what each node does

A node is one server. In this guide, **A** is where you change shared rules and
settings; **B** receives them automatically. **Keepalived** moves a shared virtual
IP address (the **VIP**) to the healthy node. Clients use the VIP as their proxy
address, so they do not need a different address after takeover.

These are two separate jobs:

| Job | How it works |
| --- | --- |
| Change and copy policy | Publish on A; B fetches and applies it automatically |
| Handle client traffic | The healthy node holding the VIP serves clients |

If B takes over traffic, it stays read only for shared policy. Accounts,
passwords, 2FA and host-specific settings stay local. HA is not a backup of
those files, and it does not protect against both servers losing power together.

## Prepare the network

Before continuing, read [Understand failover protection](#understand-failover-protection).

Before opening the setup form, collect these values from your network plan:

| Value | What to supply |
| --- | --- |
| A and B addresses | Two fixed IPv4 addresses on the HA network |
| Network interface | The connection on each server that carries the HA network; its name may differ between servers |
| VIP with prefix | A third, unused address reserved outside DHCP, including the subnet size |
| Allowed client network | The network whose devices should be able to use the proxy |
| VRRP ID | The same ID on both nodes, distinct from other VRRP groups on that network |

Use your real network values. A suggested VIP is not a reservation. The check
can detect a responding owner, but it cannot reserve an address or prove that a
silent device will never use it. Ask your network administrator if unsure.

Both hosts need active UFW with incoming traffic denied by default. Check that
your management access works and that the anti-lockout profile shows the right
sources and ports. Keep a second SSH session or console available. Conversion
adds narrow HA rules; it does not open unrestricted outbound internet access.

## Create A, then join B

1. On **A**, open **Settings â†’ High availability â†’ Set up HA** and choose
   **Create a new HA cluster**.
2. Select the detected HA connection. Check every filled value, enter B's fixed
   address, and review the VIP and allowed client network.
3. Review the proposed changes and firewall rules. Confirm with your current
   password and, if enabled, your authenticator code. Wait for completion and
   sign in again after the console restarts.
4. On A, choose **Add node** and copy the pairing code. It expires after ten
   minutes. Treat it like a password; do not paste it into logs or tickets.
5. On **B**, choose **Set up HA â†’ Join using a pairing code**. Paste the code,
   select B's own HA connection, review the changes and confirm.
6. Wait for completion, then sign in on B again.

The existing setup is backed up before conversion. If conversion fails, read
the reported result before retrying. The [installation guide](installation.md#join-existing-standalone-nodes-through-the-web-interface)
explains recovery and service checks.

## Verify before handing over

- In **Settings**, the published and applied revisions should match. A revision
  is an identifier for a version of the shared policy. Allow time for B to fetch it.
- Publish one harmless test change on A. Confirm it appears on B, then restore it.
- Test an allowed and a blocked website from a real client using the **VIP**.
- In a maintenance window, stop A's proxy and verify that B takes over the VIP
  and serves that client. Restore A and check both status displays again.
- Record the management addresses, the VIP, recovery access and backup location.

A matching revision proves policy agreement, not that client traffic works.
Do both checks. See [Troubleshooting](troubleshooting.md) if they disagree.

## Remove a node carefully

!!! note "Since v1.4.0-dev.1 (pre-release)"

    The prerelease requires your current administrator password when revoking
    pairing, plus a fresh authenticator code if you enabled 2FA. This additional
    confirmation is not part of stable v1.3.0.

To revoke synchronization, open **Settings â†’ High availability â†’ HA maintenance
â†’ Revoke pairing**. Revocation on A invalidates the peer credential;
revocation on B removes its local pairing. Check both nodes and revoke on both
before deliberately pairing them again. Their last active proxy policy remains
in place. Generate a new invitation on A and join from B; a new invitation
replaces any unused previous invitation. Redeemed and expired codes cannot be
used again. Keep the node clocks synchronized for invitations, 2FA and certificates.

Revoking a pairing stops synchronization; retiring a node stops its local HA
and proxy services. They are different actions. Before retirement, verify that
the other node already serves clients through the VIP. Follow
[Retire an HA node](installation.md#retire-an-ha-node). The console remains
available, and dedicated UFW anti-lockout rules are retained.

## Understand failover protection

The setup described here installs **Keepalived failover and automatic policy
synchronization**. It does not automatically install Pacemaker/Corosync, a witness
or fencing. Upgrading to v1.3.0 does not add those components to an existing cluster.

If the interface shows **Failover protection â†’ Quorum and fencing are not
configured**, that is expected for this setup. It describes cluster protection,
not whether rules are synchronized. Check the policy revisions separately.

**Split brain** means both nodes act as the active node. If they lose contact
while both can still reach clients, VRRP alone cannot guarantee that only one
owns the VIP. Secure policy synchronization does not prevent this situation.

| Protection | Purpose | Current status |
| --- | --- | --- |
| Keepalived/VRRP | Move the VIP when a node or proxy becomes unavailable | Configured by the normal HA workflow |
| Quorum with an independent witness | Decide which side may continue after loss of contact | OS-prepared experimental integration; guided provisioning is planned |
| Fencing | Stop or power off a node before permitting an unsafe takeover | Requires platform-specific OS configuration and testing |

A witness must have an independent failure domain. Fencing credentials must be
limited to the intended nodes, and the power-off path must actually be tested.
ProxyPermit does not guess those settings or silently change your platform.

On an OS-prepared protected cluster, the console reports recent root-verified
evidence. Missing or expired evidence is shown as uncertain; it is never retained
as healthy indefinitely. This status does not replace a real fencing test.
See [protected-cluster maintenance](administration.md#maintenance-on-an-os-protected-cluster)
and the [roadmap](planned-work.md) for the supported boundary and remaining work.

### Prepare a protected cluster (pre-release)

System administrators can open **Settings â†’ High availability â†’ Prepare split-brain
protection**. The checks are local, root-exported and expire after 90 seconds.
They cover managed HA, the OS time-synchronization report, cluster packages,
the staged plan and the fencing agent. They do not enable quorum or prove that
the peer/witness is ready. Authentication and an actual power-off/recovery test
remain separate requirements; a copied plan never marks those tests complete.

Once a node is reserved for cluster-managed HA, this initial preparation panel
is hidden. Use the current **Failover protection** status instead. Experimental
OS-coordinated activation was verified in the disposable Proxmox lab; see the
[verification report](verification.md#native-quorum-activation-in-the-disposable-proxmox-lab).
The web interface does not yet provision that protected cluster automatically.

The OS administrator reviews a secret-free plan, for example:

```json
{
  "version": 1,
  "platform": "proxmox",
  "witness": "192.0.2.13",
  "fenceAddress": "192.0.2.100",
  "nodes": [
    {"name": "proxy-a", "address": "192.0.2.11", "target": "201"},
    {"name": "proxy-b", "address": "192.0.2.12", "target": "202"}
  ]
}
```

Use actual values, with A first and B second. The witness must be outside both
nodes' failure domain. An IP address alone cannot prove that independence.
From the installed application directory, validate first and stage separately:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_setup --plan /root/cluster-plan.json
sudo .venv/bin/python -m scripts.deploy.cluster_setup --plan /root/cluster-plan.json --stage
```

Staging saves root-only `/etc/proxypermit/cluster-plan.json`. It does not change
UFW, Keepalived or Pacemaker. An unsynchronized clock, missing dependencies or
an agent without the required authentication support must be resolved before
activation. Guided provisioning, joining and migration are still in development.

### Proxmox lab fencing credentials (experimental)

The optional `scripts/dev/proxmox_fencing.py` helper runs on the Proxmox host.
Its default is a scope preview. With explicit `--apply`, it creates a new user,
role and two privilege-separated tokens; each token can manage only the peer VM
in the reviewed existing pool. It refuses existing identities/output directories
and verifies effective permissions before allowing delegated file access.
Partial state is retained for root review after failure. It never powers off a VM.

The role contains `VM.Audit` and `VM.PowerMgmt`; Proxmox's latter privilege also
permits starting/resetting that peer. It grants no host administration or access
to other guests. Tokens expire after 14 days by default: arrange replacement
before expiry. This helper prepares lab credentials, not production token rotation.

Some distribution versions of `fence_pve` lack API-token support. The opt-in
`scripts.deploy.proxmox_fence` module installs a separately named, pinned
ClusterLabs backport after checking its source hash. See
[the included source and license](https://github.com/Dantdmnl/ProxyPermit/tree/main/deploy/fence-agents).
The application account must not read `/etc/proxypermit/proxmox-fencing.json`.
That root-only profile contains `version`, `host`, `pveNode`, peer `target`,
token `expires`, `tokenId` and `token`. The fixed public CA file is
`/etc/proxypermit/proxmox-fencing-ca.pem`. The endpoint must match its certificate
hostname. Never enable unverified TLS or place tokens in CIB/CLI arguments.

The backport permits only status, monitor and off against the configured peer.
Plan its installation without `--apply`; install explicitly with `--apply`.
The distribution agent is preserved. Test the actual agent's off and recovery
path before enabling a protected cluster. Source integrity and metadata alone
do not establish working fencing.
