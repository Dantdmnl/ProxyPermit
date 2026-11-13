# Follow-up work

[Nederlands](../nl/vervolgwerk.md) · [Documentation](README.md)

Updated on 13 November 2026. These items are planned, not implemented features.

## Priority 1: safe behavior during HA failures

- Design split-brain protection. Explicitly choose a quorum/witness design with
  enforceable exclusion, or fencing. Pinging a third host or adding another
  heartbeat alone does not guarantee exclusive VIP ownership.
- Define when a node may own the VIP, when it must stop, and how operators recover
  when the peer or decision mechanism is unavailable.
- Keep VIP ownership separate from shared-policy write authority: failover must
  not silently promote B to policy writer.
- Clearly expose uncertain cluster state, lost peer contact and mismatched policy
  revisions, and audit state changes.

**Acceptance:** use separate VMs to test complete and asymmetric network
partitions, loss of the witness/fencing service, a hung proxy, reconnection and
return of an outdated node. Verify exclusive VIP ownership within the chosen
failure model, documented stop behavior and preservation of the last valid policy.
Two active nodes must not be presented as healthy. Document the guarantee's limits.

## Priority 2: activate changes without breaking connections

- Determine which changes each supported Tinyproxy version can reload through
  `SIGUSR1` and which require a full restart. Check the systemd unit and process
  startup mode as well.
- Use reload for proven compatible changes, both on A and when applying policy
  on B. Keep validation, saving, activation and recovery separate.
- Explicitly identify changes requiring a restart. Never silently fall back to a
  disruptive restart after a failed or uncertain reload.
- Verify that the new configuration actually works; successful signal delivery
  alone does not prove successful activation.

**Acceptance:** keep long-running HTTP transfers and HTTPS CONNECT tunnels open
while adding, removing and synchronizing rules. Existing connections survive
supported reloads; new connections follow the new policy. Test invalid
configuration, refused reloads and recovery. Document that preserving existing
tunnels means new filter rules do not retroactively close those tunnels.

## Priority 3: HA key and certificate management

- Add controlled peer-key rotation with a bounded transition period and
  verification before revoking the old key.
- Test revocation on both nodes, re-pairing and rejection of expired or consumed
  invitations. Expose synchronization failures without logging secrets.
- Design safe certificate renewal and pin changes. A pin mismatch must never
  trigger automatic disabling of certificate verification.
- Audit who pairs, rotates or revokes credentials, on which node and with what
  result. Never include bearer keys, invitations or 2FA secrets in audit records.

**Acceptance:** revoked keys no longer work; interrupted rotation leaves a
recognizable, recoverable state. Unexpected certificates are rejected. Policy
remains unchanged on authentication or transport failures.

## Required regression tests for these improvements

- Interrupt transfers and reboot during each activation phase.
- Test full disks, missing write permissions, invalid/incomplete responses,
  concurrent publications and lagging revisions.
- Test changed, unexpected and expired TLS certificate material. Explicitly
  define and document which validity checks certificate pinning performs.
- Verify atomic saves, rollback, locking, account permissions and anti-lockout
  rules. Accounts and host-specific configuration remain local.
- Cover standalone operation and paired HA nodes, with understandable NL/EN
  errors. Record results and remaining limitations.

## Evaluate later: additional message authentication

Assess whether per-request HMAC-SHA256 with timestamps and one-time nonces
addresses a concrete additional threat on top of TLS. Current automatic HA sync
uses pinned TLS, a peer key and SHA-256 revisions; legacy sync already uses HMAC.
HMAC does not replace TLS, key rotation, quorum or fencing. A stolen HMAC key can
also be used to generate fresh valid requests. Implement only after a justified
assessment and replay/clock-skew tests.

## Separate track: optional account replication

- Design optional account replication separately from proxy policy replication.
  Decide which accounts remain local, how existing usernames conflict, whether
  existing password hashes may be replaced, how revocation and session versions
  propagate, and what happens to 2FA secrets and recovery access. Do not silently
  overwrite an existing local account. Current accounts are not replicated.
