# Logging, privacy and retention

[Nederlands](../nl/logging.md) · [Documentation](README.md)

## What is retained

Managed installations use these defaults. Age limits are maximum retention
windows, not a promise that every record remains available for that duration.
A storage limit can remove older records sooner.

| Source | Age | Storage control |
| --- | --- | --- |
| Administrative and authentication audit | 30 days | 2 MiB active file and five numbered backups: up to 12 MiB of record content |
| ProxyPermit and managed Keepalived diagnostics | 30 days | Dedicated `proxypermit` journal namespace, 100 MiB budget |
| Tinyproxy diagnostics and optional traffic logs | 7 days | Dedicated `proxypermit-traffic` journal namespace, 32 MiB budget |

Audit cleanup checks record timestamps, including the active file and every
numbered archive. It runs hourly and before writes when cleanup is due, so a
quiet active file does not retain old personal data indefinitely. Malformed
records without a reliable timestamp are removed during maintenance; only a
count is reported. Rotation, cleanup and archive reads share a process-safe
lock. Oversized audit files and excess backups are reduced to the configured
storage limits, keeping the newest complete records.

Journal storage is persistent. Journal segments rotate at least hourly;
an hourly timer rotates and vacuums only the two owned namespaces. Deletion is
at file granularity, so physical deletion can lag the configured age until the
next rotation/cleanup. The GUI applies the age cutoff to individual records.
A powered-off host catches up when its persistent timer runs after startup.
See [journal retention settings](https://manpages.ubuntu.com/manpages/noble/man5/journald%40.conf.5.html).

New installations use Tinyproxy `LogLevel Warning`: request/connection logging
is not enabled by default. Existing native configuration survives upgrades.
Enabling `Connect`, `Info`, or `Debug` can record client IPs, destinations and
other personal information in Tinyproxy's native logs. Interface masking is
not erasure of those original records.

## Search, dates and archives

Open **Live logs → Date range**, enter local start/end times and apply.
The browser sends UTC timestamps. History pauses automatic refresh; **Back to
live** clears the date range. Source, severity and text filters combine with
the date range. Numbered audit backups and archived journal segments are
searched alongside active logs.

Pagination uses a fixed upper timestamp so newly arriving records do not push
older pages around. Rotation is serialized with audit reads. Retention can
still remove records while browsing; this is not a permanent snapshot.
Queries have record, byte and time limits. An incomplete-result notice means
that a source was unreadable, a record was malformed, or a scan limit was
reached. Narrow the date range and check source permissions. A bounded result
count is not the total size of all retained history.

The reader also supports the standard Tinyproxy file
`/var/log/tinyproxy/tinyproxy.log` and numbered/gzip backups. File logging has
its own operator/package logrotate policy; the namespace policy does not
expire those files. Other custom file paths are not exposed through the GUI.
They require an operator-managed reader and retention policy. Do not configure
multiple rotation jobs for one file, and avoid `copytruncate` for audit logs:
[logrotate documents the possible loss window](https://man7.org/linux/man-pages/man5/logrotate.conf.5.html).

## Personal data and the notice

Audit records retain the administrator username, event, time and necessary
change metadata for accountability. Defaults mask IPv4 to /24 and IPv6 to /64
before audit storage. Rule payloads use a keyed digest; descriptions are not
copied into default audit records. Passwords, OTPs, tokens, cookies and
Authorization fields are excluded. Audit records are not also copied to the
operational journal. The unused, unbounded full-rule addition log is no longer
written; managed maintenance expires old records in its standard state path.

Historical log responses and exports have defense-in-depth redaction for
credentials and URL query strings. This does not delete the source records.
Downloads leave the managed retention boundary: store them securely and erase
them under your organization's policy. Log access requires an authenticated
administrator. The application has no public log endpoint.

A short notice appears at sign-in and in the log viewer. `/privacy` is public
and describes categories, purposes and configured periods without disclosing
accounts or network topology. Set the organization's actual contact and policy:

```dotenv
PRIVACY_CONTACT=privacy@example.com
PRIVACY_POLICY_URL=https://example.com/privacy
```

The deployment organization determines the controller, lawful basis,
recipients, rights procedure and any other information required by its actual
processing. Complete the organization notice before production. This is not a
consent prompt or a claim of automatic GDPR compliance. The GDPR requires
purpose-based minimization and storage limitation; it does not prescribe a
universal log retention period. See [GDPR Articles 5 and 13](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng/)
and the [Data Protection Commission explanation](https://www.dataprotection.ie/en/faqs/responsibilities-data-controllers/how-long-should-personal-data-be-held-meet-obligations-imposed-gdpr).

## Configure and verify

Settings shows the configured periods. Change `/etc/proxypermit/.env` and run
an installer upgrade to regenerate the journal policy and timers:

```dotenv
AUDIT_LOG_RETENTION_DAYS=30
LOG_RETENTION_DAYS=30
PROXY_TRAFFIC_RETENTION_DAYS=7
AUDIT_LOG_MAX_BYTES=2097152
AUDIT_LOG_BACKUP_COUNT=5
LOG_IP_MODE=masked
AUDIT_ENTRY_MODE=hash
HTTP_ACCESS_LOGS=false
```

`LOG_IP_MODE=full` and `AUDIT_ENTRY_MODE=full` are explicit choices that increase
personal data collection. `hash` is pseudonymization, not anonymization.
Gunicorn access logging is disabled by default; if explicitly enabled, it
omits client addresses and request query strings. External reverse proxies
need their own access-log and retention settings.

```bash
sudo bash install.sh --upgrade --plan
sudo bash install.sh --upgrade --apply
systemctl list-timers 'proxypermit-*-retention.timer'
systemctl show proxypermit-audit-retention.service -p Result -p ExecMainStatus
systemctl show proxypermit-journal-retention.service -p Result -p ExecMainStatus
sudo journalctl --namespace=proxypermit --disk-usage
sudo journalctl --namespace=proxypermit-traffic --disk-usage
```

The upgrade restarts local Tinyproxy and managed Keepalived to attach their
logging namespace. Upgrade HA peers one at a time. A policy reduction can
permanently remove logs; application rollback does not recover expired logs.
Retention jobs reference a retained release and continue after a normal
uninstall, which preserves logs, configuration and releases. Do not remove
that release while the audit-retention service still references it.

**Upgrade boundary:** previous host journal/syslog records, custom log files,
configuration/recovery backups and exported downloads are not silently erased
or brought under these timers. Review their existing retention separately.
The installer never vacuums the default host journal. This preserves other
services' records. Manually deployed/legacy installations must configure their
own storage retention; the public notice identifies that unmanaged state.
