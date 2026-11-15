# Roadmap

[Nederlands](../nl/vervolgwerk.md) · [Documentation](README.md)

Where ProxyPermit is heading, what you can use today, and what still needs work.
Updated on 15 November 2026 for **v1.3.0**. Priorities may change; there are no
promised delivery dates. A checked item means that specific step was verified,
not that every feature in its milestone is production-ready.

## Available in v1.3.0

| Area | Status | What this means for you |
| --- | --- | --- |
| Two-node HA | Available | Keepalived provides VIP failover; B automatically receives policy published on A. Accounts and host settings stay local. |
| Peer-key rotation | Available after upgrading both nodes | Rotate from A's interface with password confirmation and optional 2FA; B confirms the new key automatically. |
| Internal HA certificate renewal | OS-administrator operation | Renew the pinned HA certificate through a staged handover and recovery record. This does not enable HTTPS on the management website. |
| Connection-preserving filter reload | Opt-in | Tested with Tinyproxy 1.11.1 and 1.11.3. Other settings still require a restart. Reload remains disabled by default. |
| Quorum, witness and fencing | Experimental integration | Tested on a separate KVM cluster. Initial setup still requires OS cluster administration; the normal HA wizard does not provision it. |
| Protected-cluster maintenance | Experimental integration | Rolling app upgrades and recovery are verified on an OS-prepared cluster. Complete installation/removal and other platforms remain open. |

**Why does the interface say “Quorum and fencing are not configured”?**
The normal HA setup configures Keepalived and policy synchronization. It does not
yet configure a witness or fencing. The message is expected on those installations
and does not mean policy synchronization is broken. Two-node VRRP alone cannot
guarantee exclusive VIP ownership during a network partition.

See [HA setup and its limits](high-availability.md#understand-failover-protection)
before choosing a production deployment. Upgrading does not silently convert an
existing HA installation to a quorum-managed cluster.

## Next: guided split-brain protection

**Goal:** help an administrator provision and maintain a cluster that can stop
or power off a node before allowing unsafe takeover.

- [x] Test the Pacemaker/Corosync design with a TLS witness and restricted libvirt fencing.
- [x] Verify hung proxies, leftover VIPs, asymmetric/full peer partitions, witness loss and unavailable fencing in separate VMs.
- [x] Show expiring protection evidence without exposing cluster credentials to the web account.
- [x] Verify exclusive rolling-maintenance reservations, peer takeover and explicit recovery after failure.
- [ ] Add guided initial provisioning and joining, with clear witness and fencing prerequisites.
- [ ] Add safe migration from an existing Keepalived-only installation and a reviewed recovery path.
- [ ] Complete cluster-aware rollback, node retirement and uninstallation; preserve management access and remove only owned firewall rules.
- [ ] Validate platform-specific fencing and narrowly scoped credentials for additional hypervisors and physical hosts.
- [ ] Complete end-to-end recovery tests for stale nodes, interrupted setup and unavailable decision infrastructure.

**Before calling this ready:** demonstrate exclusive VIP ownership within the
documented failure model, withhold takeover when required fencing cannot be
confirmed, and keep B read-only for policy after traffic failover. A witness on
the same physical host does not provide an independent failure domain.

## Next: complete reload and credential recovery testing

**Goal:** make routine policy changes less disruptive without weakening recovery
or peer authentication.

- [x] Preserve continuous HTTP transfers and CONNECT tunnels during supported filter reloads.
- [x] Verify automatic A-to-B application, publication ordering and native read permissions without exposing management secrets.
- [x] Verify real peer-key rotation and pinned certificate renewal on the HA pair, including old-key/pin removal.
- [x] Reject wrong or expired certificates and certificate changes between handshakes before sending the peer key.
- [x] Test isolated storage failures and lost commit replies.
- [ ] Finish VM power-loss/restart tests in every key, certificate and activation phase.
- [ ] Complete full-disk, permission, interrupted-transfer and concurrent-publication regression coverage.
- [ ] Complete revocation, re-pairing and one-time invitation lifecycle acceptance on both nodes.
- [ ] Review actor attribution and failure reporting across every credential workflow.
- [ ] Enable reload by default only after the remaining acceptance checks pass.

Existing tunnels remain open during a supported reload: a new filter rule does
not retroactively close them. See [Reload configuration](administration.md#optional-filter-reload)
and the [verification report](verification.md) for tested scenarios and limits.

## After that: HTTPS management by default

**Goal:** offer a secure management URL on fresh installations and an explicit,
recoverable migration for existing HTTP deployments.

- [ ] Generate a unique self-signed certificate and protected private key per node; display the fingerprint through the trusted installer/console.
- [ ] Support administrator-provided certificates and internal CAs; assess optional ACME where appropriate.
- [ ] Add expiry warnings and controlled renewal, separate from HA replication pins.
- [ ] Preserve anti-lockout access during migration; validate TLS before activation and provide rollback.
- [ ] Apply HSTS only to verified HTTPS responses; review secure cookies, redirects and server-identification headers.
- [ ] Test installation, upgrade, trust, renewal and recovery on standalone and HA nodes in NL/EN.

## Later: account replication and additional authentication

- [ ] Design optional account replication with explicit handling of existing usernames, password changes, 2FA, session revocation and local recovery accounts. Never silently overwrite a local account.
- [ ] Assess whether request-level HMAC-SHA256 adds useful protection beyond pinned TLS. Require replay and clock-skew tests before adopting it.

These are design topics, not currently supported features. HA policy replication
does not replicate accounts. HMAC would not replace TLS, key rotation, quorum or fencing.

## Follow progress or contribute

Read the [release notes](https://github.com/Dantdmnl/ProxyPermit/releases) for
shipped changes and [verification](verification.md) for evidence and remaining
limits. Use [GitHub issues](https://github.com/Dantdmnl/ProxyPermit/issues) to report
reproducible problems or discuss a planned feature. Include the version and
deployment type; never include pairing codes, passwords, keys or private logs.
