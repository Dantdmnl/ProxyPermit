# Verification report

[Nederlands](../nl/verification.md) · [Documentation](README.md)

## 6 November 2026

Ubuntu 26.04.1 LTS on WSL 2 ran systemd, Python 3.14.4, and Tinyproxy 1.11.3.
The tests covered Gunicorn 26.2.0 and Granian 2.8.7 with two workers, real HTTP
requests, sign-in, configuration writes, CSRF, revision conflicts, and assets.
The installer deployed Granian, upgraded to Gunicorn while preserving settings,
and rolled back to the previous release. Tinyproxy returned HTTP 403 for a
blocked destination and HTTP 200 for an allowed destination. File writes and
restarts worked under the service account.

A listener configured only in the systemd unit was lost on upgrade. The
installer now migrates it into the environment and probes the configured HTTP
endpoint, Unix socket, or local TLS endpoint. The TLS probe checks reachability.

The initial run passed 44 Python tests. After reorganizing the repository,
58 Python tests and JavaScript locale and rendering checks passed. Both HTTP
servers were retested. The workflow passed local actionlint validation, and
pip-audit reported no known vulnerabilities in the production dependencies at
the time. The hosted workflow was still pending.

## 8 November 2026

- The installer upgraded the standalone WSL installation at
  `/opt/proxypermit-integration`. Windows could open the console on loopback
  port 8098. The unauthenticated configuration API returned 401. Real proxy
  traffic returned 403 before adding a test rule and 200 afterward. The
  original rules were restored.
- A two-node lab with hand-configured network namespaces verified console
  access and proxy traffic on both nodes. After Tinyproxy stopped on node A,
  node B took over and served a new request through the virtual IP after 2.1 s.
- A separate lab checked both installer-generated Keepalived configurations
  with `--config-test`. With the generated health check, a request through the
  same virtual IP returned 200 after 3.2 s when Tinyproxy stopped on node A.

The failover tests shared one WSL host. Outstanding checks include Ubuntu
24.04, upgrade from a production legacy cluster, Nginx/TLS client access,
and a full HA install and network-partition test on separate Linux hosts.

## Reproduce the checks

```bash
python scripts/dev/test.py
python scripts/dev/check_server.py --backend gunicorn
python scripts/dev/check_server.py --backend granian
```

The HA lab requires a Linux test host with Tinyproxy, Keepalived, iproute2, and
the dependencies in `requirements/granian.txt`. Start it from the repository
root with the virtual environment in `.venv`.

```bash
sudo unshare --mount --propagation private .venv/bin/python scripts/dev/ha_lab.py --hold
```

Two isolated network namespaces use a dedicated bridge. The consoles listen on
loopback ports 8101 and 8102 and the proxy on port 8130. The startup test covers
2FA sign-in, replay rejection, and new HTTP traffic after failover. The lab
prints paths to root-only credential files. The management consoles use demo
mode to avoid restarting host services. Tinyproxy and Keepalived run as real
processes. The lab does not exercise automatic policy synchronization.

## 11 November 2026: managed HA and log retention

Two disposable Ubuntu 24.04 VirtualBox guests were converted from standalone
through reviewed GUI plans and a one-time pairing code. Both retained UFW
incoming/outgoing deny defaults. Published filters and shared Tinyproxy settings
arrived automatically on B. Actual proxy requests passed through the VIP on A
and after failover to B. Automatic failure recovery and manual standalone
recovery were exercised. These VMs share one physical host; this does not prove
physical host-loss recovery, fencing, or partition safety.

The new audit and journal retention services ran on a real guest. Synthetic
31-day records were removed from both active and rotated audit files, while
2-day records remained searchable through a UTC date range. The operational
and Tinyproxy journal namespaces were readable through the authenticated API.
Vacuuming those namespaces preserved a sentinel in the default host journal.
Both namespace budgets and successful maintenance service results were checked.

Regression tests cover parallel audit writers/rotation, age and size cleanup,
archive reads across rotation, anchored pagination, malformed records,
IPv4/IPv6 and credential redaction, private-log path restrictions, access
control, and public notices. Playwright covers NL/EN, both themes, mobile logs,
date-filter requests, history/live transitions, and destructive-button contrast.
The deployment organization must still validate its own privacy notice,
external logs, backup/export retention, network plan, and workload.

## 12 November 2026: operator workflows and HA responsiveness

The working build was checked with the full project checks and isolated browser
fixtures. The Windows Python suite runs 144 tests with 7 platform-specific skips;
the Linux installer runs the full suite during each lab upgrade. Playwright covers
EN/NL, light/dark themes, small viewports, keyboard interaction, backup write
blocking and the reviewed filter-mode draft workflow. Documentation screenshots
are generated from isolated fixtures with `PP_DOC_SCREENSHOTS=1`.

On the running VirtualBox lab, rule order was changed on A and verified in B's
already-open page without manual reload, then restored. Tinyproxy on A was
temporarily stopped with SIGSTOP while Keepalived stayed active. B acquired the
VIP, its page reported the active role, and a client received an HTTP response
through the VIP. A resumed automatically, recovered the preferred VIP and the
backup display returned to standby. A separate rescue timer also sent SIGCONT.
The standalone GUI activated the opposite filter mode and restored its original
configuration. Both HA nodes remained paired and policy synchronization recovered.

These checks do not establish production availability across a physical network
partition, upstream outage or shared hypervisor failure. Account replication and
granular roles were still separate work at this stage. The later [role implementation](roles.md) adds restricted local access.

## 12 November 2026: anti-lockout and visible drafts

The standalone and both HA lab nodes were upgraded with a reviewed single-host
management source, their actual SSH/web ports and a root-owned safety profile
(mode 0640, service-group readable). Existing firewall activation state was kept.
The empty-firewall installation path exposed an invalid numeric insert position;
it rolled back, and the corrected prepend operation passed on that same host.

Authenticated API attempts to remove the protected Allow block, change the pinned
proxy port or put a Deny before the management exceptions were rejected without
changing the active file or creating a draft. A shared Timeout change was then
published on A, reconciled on B with B's local safety block intact, and restored
on both nodes. The complete real failover/recovery and rule-order checks passed
again after the upgrades. Dedicated UFW safety rules are excluded from HA cleanup.

Browser checks cover unsaved/saved/active draft states, the redacted difference
view, save/discard actions and the existing theme/language/mobile matrix.
The installer review and non-root handoff retain the explicit protected addresses
and host-side ports. Windows checks run 144 Python tests with 7 platform skips.
Destination filters and proxy authentication still apply: this protects the
configured network access, not arbitrary routing or upstream policy changes.

The final Linux run passed all 144 Python tests without skips, the shell checks,
and isolated HTTP/account/2FA/audit/save checks with both Gunicorn and Granian
(two workers each). The extra backend was installed in a separate test venv;
the live deployment dependencies were kept intact.

## Documentation website

The Zensical site builds with `--clean --strict`, including link and anchor
validation. The same build was checked from a generated release archive.
Playwright checks cover English/Dutch guides, light/dark mode, desktop/mobile
layout, translation links, real search results and keyboard closing. The local
preview uses the `/ProxyPermit/` project prefix to check Pages-relative paths.
Actionlint validates the Pages workflow. This prepares publication; it does not
mean GitHub Pages has already been enabled or deployed.

## 12 November 2026: local account roles

The Windows suite passes 153 Python tests (7 platform-specific skips); the Linux
installer runs all 153 without skips. The complete checks also pass for typing,
Python quality, frontend contracts, shell syntax and isolated HTTP/account/2FA
checks with Gunicorn and Granian. Browser coverage includes monitor and proxy
manager interfaces, NL/EN, both themes, mobile layout, role selection and session
revocation. Existing role-less accounts retain administrator access.

The three VirtualBox nodes were upgraded. Temporary monitor accounts were
created through the authenticated API, promoted and removed again. The old
sessions became invalid while passwords remained usable. Monitors could not
read native configuration or restart Tinyproxy; proxy managers could restart
standalone Tinyproxy but could not administer HA or accounts. The paired backup
kept shared policy read only, and both HA status panels remained synchronized.
Attempts to change the service user were rejected without creating a draft or
changing active files. Isolated tests also cover protected system drafts,
optional monitor 2FA and last-administrator protection in the CLI.

All three SSH connections report `10.0.2.2` as their source through VirtualBox
NAT. The shared management-source display is therefore expected in this lab;
it is not each node's own IP address. Source restrictions do not replace sign-in.

Monitor rule-layout regression checks cover full-width text at 320, 390 and
1440 pixels in both languages and themes. Filter exports are now reserved for
proxy/system administrators; direct monitor export requests return 403. These
checks passed on all three lab nodes using temporary accounts that were removed.

## Release documentation and historical Actions cleanup

The release instructions now separate preparation, CI, tagging, GitHub Release
assets and Pages deployment. Account-migration examples explicitly create an
admin before enabling local accounts. A strict documentation build is a required
dependency of release packaging. Actionlint, version checks and clean/strict
Zensical builds passed, including a build from the generated source archive.

On 12 November 2026, 39 completed runs of the obsolete Pylint workflow were
removed. A before/after inventory confirmed that all 46 current workflow runs
and 61 current artifacts were retained. No current artifacts belonged to the
obsolete runs. An old commit still resolved directly by SHA afterwards: Actions
cleanup does not erase GitHub source history. These changes do not create a
release, move a stable tag or establish a new remote CI result.

## 13 November 2026 — 1.2.1 regression checks

The Windows run passed 159 Python tests (seven platform-specific skips), frontend
checks, Pylint, installer type checks for Windows/Linux and the Granian HTTP
integration checks. Isolated browser tests confirmed that stale edits and
deletions preserve the newer rule, including a background refresh while the
editor or confirmation is open. The check runs in English and Dutch.

Regression tests also cover simultaneous edits from one revision, drafts or a
changed filter mode appearing before a waiting write obtains its lock, malformed
requests and import skip counts. Existing HA tests cover primary publication,
standby write rejection and authenticated policy transfer. These checks do not
constitute a new failover test on separate VMs.

The strict documentation build and screenshot-viewer checks cover both languages,
both themes and 390/1440-pixel widths. Screenshots use isolated test data.

A separate Linux virtual environment with the current requirements also passed
159 Python tests (one root-only skip). The older WSL service environment still
had pyrad 2.4; its RADIUS test failures disappeared with the required 2.6.0 in
the isolated environment. No service environment was modified.
