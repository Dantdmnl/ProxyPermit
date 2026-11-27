# Roadmap

[Nederlands](../nl/vervolgwerk.md) · [Documentation](README.md)

Which improvements are coming, and what will they mean for administrators? The
main development priority is HA with protection against unsafe takeover.
Usability, HTTPS and logging are also areas of focus.

Updated on 24 November 2026, using **v1.3.0** as the baseline for released
features. There are no promised delivery dates, and the order may change.

**How to read this page:** a checked item is an implemented or experimentally
verified development step. It may still be unreleased. Unchecked items describe
remaining work. The [release notes](https://github.com/Dantdmnl/ProxyPermit/releases)
identify what has shipped; the [verification report](verification.md) records
technical test results and limitations.

## What you can use today

The following capabilities are included in v1.3.0:

| Capability | What it means for you |
| --- | --- |
| Two-node HA | Keepalived can move the shared virtual IP address (VIP) to the other node. Node A publishes filter rules and shared proxy settings; node B receives them automatically. Accounts and host settings stay local. |
| Peer-key rotation | You can renew the key used between nodes from A's interface, with password confirmation and an authenticator code when 2FA is enabled. Both nodes must support the feature. |
| Internal HA certificate renewal | An OS administrator can renew the synchronization connection's certificate through a controlled process. This is separate from HTTPS for the management website. |
| Filter reload without dropping connections | Available as an opt-in for Tinyproxy 1.11.1 and 1.11.3. Other settings still require a restart; reload is disabled by default. |

There is also an **experimental integration with a cluster configured in advance
at OS level**, including protection status and maintenance one node at a time.
The normal HA wizard does not yet configure that cluster for you.

!!! warning "Ordinary HA does not yet rule out split-brain"
    During a network partition, both nodes can believe they should be active.
    This is called split-brain. Keepalived with two nodes alone cannot rule it
    out. Upgrading does not enable additional protection automatically. Read
    [HA setup and its limits](high-availability.md#understand-failover-protection)
    before choosing a production deployment.

## Available for testing in v1.4.0-dev.4

- **Safer HA administration:** revoking pairing requires the administrator password again and a fresh authenticator code when 2FA is enabled. Protection status checks more strictly whether the cluster evidence is trustworthy.
- **Protected HA preparation:** system administrators can save and review a Proxmox plan and prepare the packages on each node in Settings. The OS administrator handles witness enrollment, fencing and activation.
- **Migration and recovery:** validate transport before moving the VIP and roll back an application release while the peer serves traffic. See the [verification report](verification.md) for the Proxmox test results.
- **Management HTTPS:** the fresh-install wizard suggests a local CA. System administrators can enable HTTPS and renew certificates from Settings. See [Management HTTPS](management-https.md).
- **Policy recovery and diagnostics:** managed Tinyproxy restores unconfirmed changes before it starts. System administrators can download a proxy and cluster status report from Settings.

These capabilities are included in the [v1.4.0-dev.4 prerelease](https://github.com/Dantdmnl/ProxyPermit/releases/tag/v1.4.0-dev.4),
not in stable v1.3.0. Use a disposable or recoverable test deployment for the
prerelease and the guide that corresponds to your installed version.

## HA with protection against unsafe takeover

**What you gain:** the other node takes over traffic only when it is safe to do
so. Setup guidance helps identify missing components and required permissions.

An independent **witness** helps decide which node may continue. Together with
the nodes, it enables a majority decision: **quorum**. **Fencing** can
power off an unreachable or frozen node before the other node receives the VIP.
This requires suitable infrastructure and limited access rights on the chosen
platform.

- [x] Provide an experimental cluster integration with quorum, a secure witness connection and fencing for configuration by the OS administrator.
- [x] Show protection status that stops reporting healthy when its evidence becomes stale.
- [x] Check prerequisites before activation and support maintenance one node at a time on a preconfigured cluster.
- [x] Prepare and download a validated, secret-free Proxmox plan for an existing managed HA pair from Settings.
- [x] Explain required access in Settings and let the second node check the same plan and fingerprint before OS preparation.
- [x] Save a reviewed Proxmox plan and prepare the fixed local cluster packages from Settings with reauthentication.
- [x] Verify the local node or witness before staging the plan, and preserve the staged plan once dependency preparation starts.
- [x] Inspect interrupted Proxmox activation and allow an explicit retry only while the reviewed VIP resource is still stopped.
- [x] Resume an interrupted migration reservation with fresh standby evidence and preserve existing reservation files if they differ.
- [x] Verify enrolled witness certificates and authenticated peer power observations, and compare the prepared transport fingerprints before moving the VIP.
- [x] Migrate an existing Keepalived pair to protected HA in a Proxmox lab and verify recovery from an interruption and a temporary witness outage.
- [x] Verify that a protected Proxmox pair retains exclusive VIP ownership and recovers after abrupt power loss of the active VM.
- [x] Explain creation and joining for ordinary HA in Settings and show the expiring migration phase on protected nodes.
- [x] Verify protected release rollback and automatic fencing of a partitioned VIP owner on Proxmox.
- [x] Verify that a network split without access to the witness removes the VIP from both nodes and recovers to a single owner.
- [x] Provide guarded local retirement and uninstall commands for a node already detached through OS cluster administration.
- [x] Verify a standby returning after several minutes offline and a separate policy catch-up after the standby misses a publication.
- [x] Document the provisioning boundary for vSphere, libvirt and physical servers and reject unsafe VMware TLS and IPMI fencing evidence.
- [x] Show role-specific guidance for creating A or joining B, and display fresh migration identifiers for comparison in Settings.
- [x] Keep detached retirement resumable after an interruption and make repeated completion safe.
- [x] Guide protected setup and enrollment in Settings for Proxmox. Show the witness, network access, permissions and local preparation checks. The OS administrator completes enrollment and activation.
- [x] Show the next local setup step for Proxmox and explain the available options for other platforms before showing platform-specific fields.
- [x] Guide controlled migration of an existing Proxmox HA pair and recovery from an interrupted migration. Verify both paths in the migration lab.
- [x] Verify that ProxyPermit recognizes an OS-managed KVM/libvirt cluster and that it fences a frozen VM before moving the VIP.
- [x] Verify local Proxmox fencing-token and witness-client certificate expiry in isolated checks on a live standby.
- [x] Verify local retirement and console uninstall on a detached protected Proxmox node while the peer serves the VIP and direct SSH remains available.
- [ ] Provide a procedure for permanently replacing a member of a protected cluster. Verify that the surviving node retains quorum, the replacement synchronizes policy and can take over the VIP, and the retired member can no longer join or fence either node.
- [ ] Support additional virtualization platforms and physical hosts, documenting each platform's capabilities and limitations.
- [ ] Extend recovery checks for power loss, network partitions and nodes returning after a long absence before offering the full setup as production-ready.
- [x] Verify controlled VIP recovery when retained monitor failures exclude both nodes. See the [Proxmox trial](verification.md#vip-recovery-and-detached-node-removal-on-proxmox).
- [ ] Diagnose the cause of intermittent VIP monitor timeouts. See the [initial observation](verification.md#vip-moved-from-b-to-a-after-a-monitor-timeout).

B remains read-only for shared proxy policy after traffic takeover. A witness
should not depend on the same point of failure as the proxy nodes. See the
[HA guide](high-availability.md) for prerequisites and the
[acceptance checks](verification.md#protected-ha-acceptance-checks-for-v140) for
the evidence required for wider use.

## Faster, clearer logs

**What you gain:** investigate problems quickly, without long waits or having
to read the same messages repeatedly.

- [x] Open the log window faster with smaller pages and a bounded scan of recent records.
- [ ] Load new records without repeatedly fetching the whole list or unnecessarily changing your reading position.
- [ ] Make searching and filtering by source, level and date fast and reliable, including archives within the retention period.
- [x] Group repeated certificate-trust messages per minute with a count and a short explanation. Raw records remain available for investigation.
- [ ] Clearly distinguish important TLS failures from repeated browser messages, so expired certificates or unsafe connections stand out.
- [x] Show when results were last fetched, flag potentially stale logs after a failed refresh or lost connection, and resume live refresh after reconnecting.

Role checks, redaction and retention limits remain in place: defaults are 30 days
for management/audit logs and 7 days for Tinyproxy/traffic logs. Certificate
verification will not be disabled to reduce messages. See [Logging](logging.md)
for the current capabilities.

## HTTPS management by default

**What you gain:** a secure management address on new installations and
certificate administration without needing the terminal for every operation.

- [x] Offer HTTPS with a certificate authority (CA) for each node in the development version, including controlled activation, expiry warnings, renewal and recovery.
- [x] Let system administrators perform these certificate operations through the web interface, with password confirmation and optional 2FA.
- [x] Suggest HTTPS with a local CA in the interactive wizard for new installations.
- [ ] Review the default for unattended installations and guide existing HTTP installations through migration.
- [x] Offer a local CA or an administrator-provided PEM certificate at installation and verify both in Ubuntu VMware guests.
- [x] Verify that a Keepalived pair retains its HTTPS configuration during an upgrade and when rolling back the standby. See the [VMware trial](verification.md#upgrade-and-rollback-on-a-keepalived-pair-with-https).
- [ ] Investigate automated renewal for externally issued certificates, including ACME where appropriate.
- [ ] Apply HTTPS security headers, including HSTS, carefully and align cookies and redirects with them.
- [ ] Complete the installation, upgrade and recovery workflow for standalone nodes and HA deployments.

A locally issued certificate must first be trusted on the computers used to
access the management interface. See [Management HTTPS](management-https.md).

## Reliable policy changes and credential renewal

**What you gain:** filter rules and shared settings reach the other node
reliably. Key and certificate changes have a clear outcome, even when the
connection drops during an operation.

- [x] Support automatic policy synchronization and optional filter reload that preserves existing connections.
- [x] Renew peer keys and internal HA certificates through a controlled process, then remove trust in the previous keys or certificates.
- [x] Further secure revocation and re-pairing in the development version, including protection against invitation reuse.
- [ ] Extend recovery checks for power loss, full disks, interrupted transfers and simultaneous changes.
- [ ] Clearly record who performed each administrative operation and whether it completed or requires recovery.
- [ ] Enable filter reload by default only after the remaining reliability checks are complete.

Existing connections stay open during reload. New filter rules therefore do
not close existing tunnels retroactively. See [Filter reload](administration.md#optional-filter-reload).

## Clearer management through the shared address

**What you gain:** after an HA takeover, know which node you are managing and
why you may need to sign in again.

- [x] Show A or B and an optional public label on the managed HA sign-in screen in the development version.
- [x] Show the fixed node identity alongside local VIP status in Settings.
- [x] Ask for a fresh sign-in when a node change is detected while signing in.
- [x] Explain a detected node change from an open management page and keep unsaved input visible until the administrator reopens the console.
- [x] Verify local accounts, optional 2FA and an open form in the mobile browser during a controlled VIP handoff.

Accounts and sessions are currently local. Traffic takeover does not
automatically make an account available on the other node.

### Managing policy when only the VIP is reachable

Some management networks allow access only to the VIP. In the current protected
setup, B can keep serving traffic after A recovers, while shared policy edits
remain on A. An administrator who reaches B through the VIP cannot add a rule.

- [ ] Make A the preferred node for management after recovery. Design automatic return of the VIP to A after a stable health window with confirmed quorum, fencing and current policy. Test that a node repeatedly dropping offline does not move the VIP back and forth.
- [ ] Keep policy management available through the VIP while A is unavailable. Design a single authenticated writer at a time. Let B take write authority only after quorum and fencing confirm that A cannot write, and reconcile policy revisions before handing authority back.
- [ ] Test from a management network that allows only the VIP. Verify that a system administrator can add a rule after takeover and after A returns, with local sign-in, role checks, audit attribution and protection against duplicate publication.

Pacemaker combines location preferences with the score for keeping a resource
on its current node. With the current settings, keeping the VIP on a healthy B
outweighs A’s location preference. Returning the VIP and handing over policy
write authority are distinct operations. [Pacemaker placement](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/constraints.html) · [Fencing](https://clusterlabs.org/projects/pacemaker/doc/3.0/Pacemaker_Explained/html/fencing.html)

## Topics for later investigation

These are design topics; the eventual implementation has not been decided.

- [ ] **Optional account replication:** manage users across nodes, with explicit choices for existing usernames and preservation of local recovery accounts. Passwords and 2FA settings must not be silently overwritten.
- [ ] **Additional message signing:** assess whether it demonstrably strengthens the existing secure synchronization and what it means for key management and recovery.

## Discuss or contribute

Report a reproducible problem or discuss a proposal through
[GitHub issues](https://github.com/Dantdmnl/ProxyPermit/issues). Describe what you
are trying to do, your version and whether you use standalone or HA deployment.
Do not share passwords, pairing codes, keys or unredacted private logs.
