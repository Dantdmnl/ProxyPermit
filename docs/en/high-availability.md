# Set up high availability

[Nederlands](../nl/hoge-beschikbaarheid.md) · [Documentation](README.md)

**For:** a system administrator with two working standalone nodes.
**Result:** clients use one shared proxy address and B automatically receives
policy published on A. Try this in the [VM lab](vm-lab.md) before production.

In the current development version, the sign-in screen and Settings identify
the management node as A or B. This
identity stays fixed when the virtual IP moves. Accounts and sessions are local
to each node. If the sign-in form detects a different node, the browser opens
a fresh form and asks you to sign in with that node's account.

If the VIP moves while the console is open, the page keeps the current form
visible and blocks further changes. Review or copy any unsaved settings before
choosing **Reopen management console**. After signing in, check the new node's
current settings before applying changes.

To add a recognizable public label, set `PROXYPERMIT_PUBLIC_NODE_LABEL` in the
node's protected `/etc/proxypermit/.env` and restart the management service
(`proxypermit.service`). Use up to 32 ASCII letters, digits, spaces, underscores
or hyphens, starting with a letter or digit. Choose a label suitable for people
who can reach the sign-in page.

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

!!! note "Since v1.4.0-dev.1 (pre-release)"

    The prerelease requires your current administrator password when revoking
    pairing, plus a fresh authenticator code if you enabled 2FA. This additional
    confirmation is not part of stable v1.3.0.

To revoke synchronization, open **Settings → High availability → HA maintenance
→ Revoke pairing**. Revocation on A invalidates the peer credential;
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

If the interface shows **Failover protection → Quorum and fencing are not
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
as healthy indefinitely. When protection needs attention, Settings identifies
which checks could not be confirmed. For example, the witness connection can be
unconfirmed while the two proxy nodes together still have quorum. Use an actual
fencing test to validate takeover.
See [protected-cluster maintenance](administration.md#maintenance-on-an-os-protected-cluster)
and the [roadmap](planned-work.md) for the supported boundary and remaining work.

### Prepare a protected cluster (v1.4.0-dev.2)

System administrators can open **Settings → High availability → Prepare split-brain
protection**. The checks are local, root-exported and expire after 90 seconds.
They cover managed HA, the OS time-synchronization report, cluster packages,
the staged plan and the fencing agent. They do not enable quorum or prove that
the peer/witness is ready. Authentication and an actual power-off/recovery test
remain separate requirements; a copied plan never marks those tests complete.

Once a node is reserved for cluster-managed HA, this initial preparation panel
is hidden. Use the current **Failover protection** status instead. Experimental
OS-coordinated activation was verified in the disposable Proxmox lab; see the
[verification report](verification.md#native-quorum-activation-in-the-disposable-proxmox-lab).
The installed web helper saves the plan and prepares the fixed packages on
each node. The OS administrator handles witness enrollment, fencing and
activation.

1. Open the direct management address of A and choose **Proxmox VE**. See
   **Required network connections** for ports. For other platforms the console
   explains the available OS-managed options.
2. Enter the Proxmox VM IDs and the IPv4 addresses of the witness and Proxmox
   API. The node addresses and local OS hostname come from the installation.
   Enter the OS hostname of the other node and select **Validate Proxmox plan**.
3. Review **Firewall rules at activation** if shown, then select **Save plan
   on this node**. Download the plan. On B, choose **Proxmox VE** and use
   **Import the plan from node A**. Compare the fingerprints.
   Save it on B too. Use the same downloaded file on the witness.
4. After the saved fingerprint appears, select **Prepare cluster packages**
   on each node. Wait for local completion. The installation keeps the
   new cluster services inactive while ordinary HA continues. **Next on this
   node** shows when to continue over SSH with witness setup and fencing tests.

Confirm each change with your password and, when enabled, your authenticator
code. **Load saved plan** reopens the local plan for review. Once dependency
preparation begins, the plan is locked on that node.

In the development build, open **Witness enrollment and protected migration**
to see which host to use, the required network paths, and the next checks.
**Local witness enrollment and Corosync key recorded** confirms the matching
local preparation records. Before migration, the OS command verifies the
current certificate and compares the key with the peer’s evidence.

If a job fails, review the private systemd log. A failed preparation can
resume with the same plan.

```bash
sudo journalctl --namespace=proxypermit -u proxypermit-cluster-web.service
```

The OS administrator can also write that same secret-free plan by hand:

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
Use the same JSON on all three hosts. To perform the local checks and staging
from the command line, run these commands in the installed directory:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_setup --plan /root/cluster-plan.json --role node
sudo .venv/bin/python -m scripts.deploy.cluster_setup --plan /root/cluster-plan.json --role node --stage
```

Expect `checkedHost: "a"` on A and `checkedHost: "b"` on B. On the witness,
use `--role witness` in both commands from the reviewed ProxyPermit application
directory. Expect `checkedHost: "witness"`. The witness needs only its own
planned address. A and B are also checked against their installed roles and
hostnames. Compare `planHash` on all three hosts before proceeding. It must
match even when the JSON files use different formatting.

Staging saves root-only `/etc/proxypermit/cluster-plan.json` without changing
UFW, Keepalived or Pacemaker. A reserved cluster uses its maintenance workflow.
Once dependency preparation starts, a different plan requires OS review.
An unsynchronized clock, missing dependencies or an agent without the required
authentication support must be resolved before activation.

### Prepare the OS and witness

If you are using the command line, preview dependency preparation on each
node and then apply it. Installation keeps the native cluster services inactive.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_bootstrap --role node
sudo .venv/bin/python -m scripts.deploy.cluster_bootstrap --role node --apply
```

Use `--role witness` on the witness. Before reserving a node, have the OS
administrator set up the mutually authenticated witness, enroll both
Corosync clients and verify the separate fencing power paths. Record the
power-off and recovery of both exact targets. Keep a direct administrative
session open to each host and check that you can restore it independently.
The preparation checklist in the web interface tracks local facts. The
site administrator coordinates these steps on the three hosts.

### Enroll the witness and verify peer fencing

On a new witness with an empty certificate store, initialize QNetd. Use
the reviewed ProxyPermit source directory on that host and keep direct
SSH available. Copy the public CA to A and B over authenticated SSH.

```bash
sudo corosync-qnetd-certutil -i
sudo python3 -m scripts.deploy.cluster_witness --apply
sudo openssl x509 -in /etc/corosync/qnetd/nssdb/qnetd-cacert.crt -noout -sha256 -fingerprint
```

Choose one operation ID with `openssl rand -hex 16` and set `operation` to
that value on both proxy nodes. Run the workspace command on A and B and
compare the plan hashes. Its `cluster` value is the certificate subject
and its `directory` is the private working directory.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_preflight workspace --operation "$operation"
```

Use the [Corosync certificate enrollment procedure](https://manpages.debian.org/bookworm/corosync-qdevice/corosync-qdevice-net-certutil.8.en.html).
Initialize each new node's NSS store with the witness CA using
`corosync-qdevice-net-certutil -i -c /root/witness-ca.crt`. On A, generate
the request with `corosync-qdevice-net-certutil -r -n "$cluster"`. Transfer
the request to the witness and sign it there with
`corosync-qnetd-certutil -s -c /root/qdevice-net-node.crq -n "$cluster"`.
Import the signed certificate on A with `corosync-qdevice-net-certutil -M -c`.
Transfer the resulting root-only PKCS#12 bundle to B over authenticated SSH
and import it using `corosync-qdevice-net-certutil -m -c`. Both import
commands take the corresponding file after `-c`. These operations are
for a new dedicated cluster. Preserve an existing certificate store for OS
review if any phase is interrupted. Some package versions return an error
after importing on B. Continue only after the local ProxyPermit check below
confirms the expected certificate and key.

Set `witness_ca_sha256` to the value after `=` in the witness fingerprint
output and run the certificate check on both nodes. Compare `certificateHash`.
Then run the key command on A, transfer its private `authkey` to the
reported operation directory on B and run the key command on B too.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_preflight witness --operation "$operation" --ca-sha256 "$witness_ca_sha256"
sudo .venv/bin/python -m scripts.deploy.cluster_preflight key --operation "$operation"
```

In a maintenance window, test the peer-only fencing agent from each node,
one peer at a time, using its configured Proxmox host and target. After
the agent turns the peer off, run `fence-off` from the surviving node.
Restore the peer through the independent Proxmox management channel, verify
its local services, then run `fence-on` on the same observer within ten
minutes. These commands query the authenticated power state of the planned
peer and record the result. Before migration, the test must be current
and use the same agent, credential and CA.

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_preflight fence-off --operation "$operation"
sudo .venv/bin/python -m scripts.deploy.cluster_preflight fence-on --operation "$operation"
```

### Resume an interrupted reservation

Plan a maintenance window and keep direct OS access to both nodes. Verify
which node currently holds the VIP. Reserve the other node first, using the
same operation ID on both hosts and after completing dependency preparation
and the fencing test. Set `operation` to that shared operation ID and run:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_native --phase reserve --operation "$operation" --experimental
```

The reservation phase emits a JSON report after it
confirms that Keepalived is inactive and the VIP is absent on that node.

Before reserving the current VIP owner, use a trusted SSH channel to transfer
the standby node’s JSON report. On the VIP owner, save it as root-only
`peer-reserved.json` in `/var/lib/proxypermit-cluster-bootstrap/<operation>/`.
The report is valid for five minutes. Run `--phase reserve` on the already
reserved node again to obtain a fresh observation if needed.
With the new preflight receipts, the VIP owner also compares both witness
fingerprints and the Corosync key fingerprint before stopping Keepalived.

If an interruption leaves a node in `reserving`, inspect both nodes with
`--phase status`, including who currently holds the VIP. Then resume with
the same operation ID. The reservation
blocks independent lifecycle actions as soon as the checkpoint exists.
If the local protection profile or the Keepalived override has changed,
resolve the difference through OS review before proceeding.
After restarting a node, wait for `timedatectl show --property=NTPSynchronized`
to report `yes` before resuming. The reservation command checks this again.

### Inspect an interrupted protected HA activation

On a reserved managed node, **Settings → Protected HA migration and recovery**
shows the local phase, operation ID and plan fingerprint from the latest OS
report. Open Settings using each node’s direct address and compare the two
identifiers. The phases may differ. If an identifier differs, is missing or
has expired, stop and check both hosts over SSH. On a fresh installation, the
HA settings guide you through creating A, joining B and checking synchronization
before protected cluster preparation.

Run this on A and B from the installed application directory:

```bash
sudo .venv/bin/python -m scripts.deploy.cluster_native --phase status
```

Compare the operation ID, plan hash, and each host’s reported phase. The
command reads the checkpoint and live cluster without modifying either.
Use the next step reported by each node:

| Next step | What to do |
| --- | --- |
| `inspect-reservation` | Check the VIP owner, the two operation IDs and time synchronization. Resume the reservation on the standby before the VIP owner. |
| `transport` | Confirm that Keepalived is inactive and neither node owns the VIP. Prepare the reviewed Corosync transport with the shared key and the enrolled witness client certificates. |
| `start` | Compare the same plan on both nodes. Start the encrypted transport on each node. |
| `inspect-transport-and-pacemaker` | Confirm two-node membership and mutual TLS to the witness. Start Pacemaker on both nodes, then stage the stopped VIP configuration on each. |
| `activate` | Confirm both nodes see ready fencing and quorum with the VIP stopped. Activate once, then finalize on the other node. |
| `finalize` | The intended CIB is active and the VIP has one healthy owner. Finish the operation on the node that reports this step. |
| `retry-activation` | The intended CIB still has the VIP stopped and the witness and fencing are ready. Review both nodes and retry on the node that requested activation. The command rechecks the fencing test, maintenance reservation and CIB before writing. |
| `verify-fencing-test` | Review the fencing test receipt. Confirm a current power-off and recovery test before proceeding. |
| `inspect-vip-health` or `review` | Investigate Pacemaker and the node logs from the OS. Leave Keepalived under cluster control and retain the checkpoint. |

To perform a native step, replace `--phase status` with the approved phase
(`transport`, `start`, `pacemaker`, `seed`, `activate`, `finalize` or
`retry-activation`). Include `--operation "$operation" --experimental`,
using the ID confirmed on both nodes.

### Fencing platforms

| Platform | Provisioning and verification |
| --- | --- |
| Proxmox VE with QEMU | The native flow builds the CIB and scoped UFW rules. Use verified API TLS and restricted peer-only power credentials. Migration, abrupt shutdown, partition fencing and release rollback were exercised in the Proxmox lab. |
| VMware vSphere | An OS administrator can supply `fence_vmware_rest` with verified HTTPS and VM names that are unique in scope. Give the service identity power control only over the assigned VMs. ProxyPermit evaluates the resulting CIB. Live VMware fencing has yet to be verified. |
| KVM/libvirt | The OS administrator configures `fence_virtd` on the host and `fence_xvm` in the cluster. Restrict access to a dedicated management network and map each node to its exact VM UUID. ProxyPermit recognizes this provider in a live OS-managed cluster. The built-in setup remains specific to Proxmox. |
| Physical servers | Configure one directly addressable BMC per server with `fence_ipmilan`, IPMIv2 LAN+, cipher suite 17, and `onoff` power handling. Isolate the BMC network. If a BMC shares the server’s power supply, provide an independently powered fencing path and test loss of the supply. The application evaluates the resulting CIB. Hardware has yet to be verified in the lab. |

For the OS-managed adapters, provision Corosync, a client-authenticated TLS
witness, Pacemaker and the ProxyPermit VIP resource independently. Test that
the peer can switch off the exact target and keep it off, and that you can
restore the host through independent access. Agent availability in the CIB
does not prove those properties.

See the upstream [VMware REST fencing metadata](https://github.com/ClusterLabs/fence-agents/blob/main/tests/data/metadata/fence_vmware_rest.xml)
and [IPMI fencing agent](https://github.com/ClusterLabs/fence-agents/blob/main/agents/ipmilan/fence_ipmilan.py).
Pacemaker documents the [network and power requirements for fencing](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/fencing.html#fence-devices).
See the [verification report](verification.md) for the live KVM/libvirt results.

### Why the VIP can move back to A

In protected HA, A is the preferred node when Pacemaker has to place the VIP.
After failover, a healthy B can continue serving it when A recovers. The
native profile assigns A a location score of `100` and gives the current
owner a resource stickiness score of `1000`.

If the VIP moves back to A, compare the move with the monitor and stop events
on B. In the [Proxmox lab trial](verification.md#vip-moved-from-b-to-a-after-a-monitor-timeout),
Pacemaker stopped the resource on B after a monitor timeout and recovered it
on A. The configured migration threshold of `1` made B ineligible for the VIP
after that failure. When a later monitor failure made A ineligible, neither
node served the VIP. Inspect both nodes’ failure counts even when both hosts
are online. See [Pacemaker resource placement](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/constraints.html)
and [failure handling](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/operations.html).

### Check a stopped or failing VIP

Open an SSH connection to each node's own address. Compare cluster membership,
the resource owner and the journal around the first failure.

```bash
sudo crm_mon --one-shot --failcounts
sudo corosync-quorumtool -s
sudo journalctl --namespace=proxypermit -u pacemaker.service -u keepalived.service --since "30 minutes ago"
```

After a full cluster restart, the native Proxmox profile uses
`wait_for_all: 1`. Both proxy nodes must see each other before quorum can form
for the first time, even if one node can reach the witness. **Failover
protection** shows this specific wait when Corosync confirms it. Check that
Corosync is running on both proxy nodes and that they can reach each other on
the cluster network. If a node is unavailable, restore it or follow your OS
cluster recovery procedure. Keep the VIP under cluster control throughout
recovery. See [Corosync votequorum](https://manpages.debian.org/bookworm/corosync/votequorum.5.en.html).

The VIP agent reports whether the Keepalived unit state was unexpected, the
address was missing, or the local Tinyproxy probe failed. If Pacemaker reports
a monitor timeout, the agent might have been stopped before it could report a
result. Fix the underlying issue and verify quorum and fencing before asking
Pacemaker to recover the VIP. Keepalived remains under Pacemaker control.

If old monitor failures leave no eligible owner, record the failures first.
After checking quorum, witness connectivity, fencing and local proxy health,
ask Pacemaker to retry only the failed monitor on one eligible host. Replace
`proxy-node-a` with that host's cluster name.

```bash
sudo crm_resource --cleanup --resource proxypermit-vip --node proxy-node-a --operation monitor --interval 5s
```

Check both nodes for a single VIP owner, then test the proxy from a client.
This procedure was verified in the [Proxmox lab](verification.md#vip-recovery-and-detached-node-removal-on-proxmox).

A rolling cluster upgrade updates the installed VIP agent when it matches the
previous ProxyPermit release. An agent customized by the OS administrator
requires its own review and update.

### Recover after local Proxmox fencing credentials expire

If Settings reports an expired local fencing credential, use that node’s own
management address and keep it out of the VIP role while restoring protection.
Have the OS administrator renew the peer-only credential on the approved
Proxmox endpoint. Keep the local profile root-only, retain the verified CA
and verify the exact peer mapping. Check the agent's authenticated status
before making the node eligible again. Compare quorum, fencing readiness and
VIP ownership on both nodes. For evidence used by a pending migration, repeat
the fencing power-off and recovery checks with the renewed credential.

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
