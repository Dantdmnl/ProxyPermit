# Follow-up work

[Nederlands](../nl/vervolgwerk.md) · [Documentation](README.md)

Recorded on 12 November 2026. These items are planned, not implemented features.

- Design optional account replication separately from proxy policy replication.
  Decide which accounts remain local, how existing usernames conflict, whether
  existing password hashes may be replaced, how revocation and session versions
  propagate, and what happens to 2FA secrets and recovery access. Do not silently
  overwrite an existing local account. Current accounts are not replicated.
