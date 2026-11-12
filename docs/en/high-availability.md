# Set up high availability

[Nederlands](../nl/hoge-beschikbaarheid.md) · [Documentation](README.md)

**For:** a system administrator with two working standalone nodes.
**Result:** clients use one shared proxy address and B automatically receives
policy published on A. Try this in the [VM lab](vm-lab.md) before production.

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

1. On **A**, open **Settings → High availability → Set up HA** and choose
   **Create a new HA cluster**.
2. Select the detected HA connection. Check every filled value, enter B's fixed
   address, and review the VIP and allowed client network.
3. Review the proposed changes and firewall rules. Confirm with your current
   password and, if enabled, your authenticator code. Wait for completion and
   sign in again after the console restarts.
4. On A, choose **Add node** and copy the pairing code. It expires after ten
   minutes. Treat it like a password; do not paste it into logs or tickets.
5. On **B**, choose **Set up HA → Join using a pairing code**. Paste the code,
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

Revoking a pairing stops synchronization; retiring a node stops its local HA
and proxy services. They are different actions. Before retirement, verify that
the other node already serves clients through the VIP. Follow
[Retire an HA node](installation.md#retire-an-ha-node). The console remains
available, and dedicated UFW anti-lockout rules are retained.
