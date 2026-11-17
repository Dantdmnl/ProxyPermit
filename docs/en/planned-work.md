# Roadmap

[Nederlands](../nl/vervolgwerk.md) · [Documentation](README.md)

Which improvements are coming, and what will they mean for administrators? The
main development priority is HA with protection against unsafe takeover.
Usability, HTTPS and logging are also areas of focus.

Updated on 17 November 2026, using **v1.3.0** as the baseline for released
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

## Available for testing in v1.4.0-dev.1

- **Safer HA administration:** revoking pairing requires the administrator password again and a fresh authenticator code when 2FA is enabled. Protection status checks more strictly whether the cluster evidence is trustworthy.
- **Protected HA preparation:** system administrators can check prerequisites. The experimental cluster integration has been extended, but complete setup through the web interface is still missing.
- **HTTPS setup through the web interface:** system administrators can prepare and renew certificates and enable HTTPS. HTTPS is not yet the installation default. See [Management HTTPS](management-https.md).

These capabilities are included in the [v1.4.0-dev.1 prerelease](https://github.com/Dantdmnl/ProxyPermit/releases/tag/v1.4.0-dev.1),
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
- [ ] Guide initial setup and node joining through the web interface, explaining the required hosts, network access and permissions.
- [ ] Help existing HA installations migrate in a controlled way, with an understandable recovery procedure if migration is interrupted.
- [ ] Complete rollback, node retirement and uninstallation for protected clusters while preserving management access.
- [ ] Support additional virtualization platforms and physical hosts, documenting each platform's capabilities and limitations.
- [ ] Extend recovery checks for power loss, network partitions and nodes returning after a long absence before offering the full setup as production-ready.

B remains read-only for shared proxy policy after traffic takeover. A witness
should not depend on the same point of failure as the proxy nodes. See the
[HA guide](high-availability.md) for prerequisites and the
[verification report](verification.md) for current test coverage.

## Faster, clearer logs

**What you gain:** investigate problems quickly, without long waits or having
to read the same messages repeatedly.

- [ ] Make the log window open and respond faster, including with a long retained history.
- [ ] Load new records without repeatedly fetching the whole list or unnecessarily changing your reading position.
- [ ] Make searching and filtering by source, level and date fast and reliable, including archives within the retention period.
- [ ] Present repeated certificate-trust messages compactly, with a count, time range and explanation of what you can do. Keep raw records available for investigation.
- [ ] Clearly distinguish important TLS failures from repeated browser messages, so expired certificates or unsafe connections stand out.
- [ ] Clearly indicate when the connection is lost or the displayed logs are no longer current.

Role checks, redaction and retention limits remain in place: defaults are 30 days
for management/audit logs and 7 days for Tinyproxy/traffic logs. Certificate
verification will not be disabled to reduce messages. See [Logging](logging.md)
for the current capabilities.

## HTTPS management by default

**What you gain:** a secure management address on new installations and
certificate administration without needing the terminal for every operation.

- [x] Offer HTTPS with a certificate authority (CA) for each node in the development version, including controlled activation, expiry warnings, renewal and recovery.
- [x] Let system administrators perform these certificate operations through the web interface, with password confirmation and optional 2FA.
- [ ] Make HTTPS the default for new installations and guide existing HTTP installations through migration.
- [ ] Support administrator-provided certificates and internal CAs; investigate automated issuance through ACME where appropriate.
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

- [ ] Show a recognizable node label before sign-in without disclosing internal network details or accounts.
- [ ] Clearly distinguish fixed node identity from current VIP ownership after sign-in.
- [ ] Explain node changes and preserve unsaved input where safe, without transferring passwords or 2FA codes.
- [ ] Complete the sign-in and session experience during takeover for different local accounts, optional 2FA and mobile use.

Accounts and sessions are currently local. Traffic takeover does not
automatically make an account available on the other node.

## Topics for later investigation

These are design topics; the eventual implementation has not been decided.

- [ ] **Optional account replication:** manage users across nodes, with explicit choices for existing usernames and preservation of local recovery accounts. Passwords and 2FA settings must not be silently overwritten.
- [ ] **Additional message signing:** assess whether it demonstrably strengthens the existing secure synchronization and what it means for key management and recovery.

## Discuss or contribute

Report a reproducible problem or discuss a proposal through
[GitHub issues](https://github.com/Dantdmnl/ProxyPermit/issues). Describe what you
are trying to do, your version and whether you use standalone or HA deployment.
Do not share passwords, pairing codes, keys or unredacted private logs.
