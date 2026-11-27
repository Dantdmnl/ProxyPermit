# Changelog

Historical deployments are not assigned retrospective release tags.

## [Unreleased]

## [1.4.0-dev.5] - 2026-11-27

Use this preview on a disposable or recoverable deployment. v1.3.0 remains
the stable release. Protected Corosync/Pacemaker HA remains experimental.
The live Proxmox replacement and failover trial is still open.

### Changed

- Block managed upgrades and protected release rollbacks while local policy recovery is pending, before taking a backup of the active files.
- Keep an installation backup from overwriting pending policy recovery. If pausing the policy writers exposes an interrupted publication, resume the current services and preserve the recovery files.
- Show when local policy recovery interrupts HA synchronization, including when the last synchronization succeeded.
- Explain in HA status and administrator diagnostics when Corosync is waiting for the two proxy nodes to see each other before forming quorum.
- Verify rollback copies before restoring an interrupted policy. Show that recovery is required in Proxy configuration and preserve saved drafts until recovery completes.
- Keep the Tinyproxy startup recovery hook installed when uninstall encounters an unfinished policy publication.
- Report invalid policy checkpoints and storage or permission failures during Tinyproxy startup recovery. Authorize a managed restart only when file-lock contention confirms an active publisher.

### Verified

- Exercised service startup, upgrade refusal and installation backup restore on a disposable Ubuntu QEMU guest. Verified both policy checksums, management HTTPS and Tinyproxy allow and deny behavior after upgrading again.
- Ran all 408 isolated tests under Linux root, plus the HA and settings browser checks and the strict documentation build.

## [1.4.0-dev.4] - 2026-11-27

This preview includes the changes recorded under the dev.3 tag, which
did not publish a GitHub Release. It is intended for disposable or
recoverable deployments. v1.3.0 remains the stable release, and protected
Corosync/Pacemaker HA remains experimental.

- Include the verified recovery of unconfirmed Tinyproxy policy after a service restart or hard VM power cut, the HTTPS installer defaults, and the system administrator diagnostics from dev.3.
- Correct the documentation browser check to match the interactive installer command. The wizard asks for elevated permissions when the reviewed plan is applied.
- Document the remaining protected HA work, including member replacement, return after extended absence, and intermittent VIP monitor failures.

## [1.4.0-dev.3] - 2026-11-25

### Pre-release status

Use this preview on a disposable or recoverable installation. Stable
deployments should stay on v1.3.0. Protected Corosync/Pacemaker HA remains
experimental. Permanent member replacement, recovery after extended
absence and intermittent VIP monitor failures still require verification.

### Verified

- Tested clean installation, upgrade from the published v1.3.0 archive, rollback and uninstall on Ubuntu QEMU guests.
- Retested interrupted publication before the filter write, after both writes and after a service restart. A hard VM power cut with unconfirmed policy also restored both original files before Tinyproxy started.
- Verified management HTTPS renewal after leaf expiry and recovery after reboot with UFW enabled.
- Tested Keepalived HTTPS upgrade and rollback on VMware and local retirement and uninstall of an already detached protected Proxmox node.

### Changed

- Recover an interrupted configuration and filter publication before managed Tinyproxy starts again.
- Allow slow management workers up to one minute to answer the installer readiness check during installation, upgrade and rollback.
- Reserve space for proxy policy rollback before activation. Restore the previous files without rewriting them if the disk fills, and leave Tinyproxy running if publication fails before activation.
- Make release archives reproducible with the same source and build toolchain. Verify the complete file manifest in CI before exercising the installer.
- Show proxy service health separately from VIP ownership in HA settings, and offer system administrators a diagnostic snapshot containing only status fields.
- Suggest direct HTTPS with a local CA in the interactive wizard for new installations.
- Report protected VIP health failures in Pacemaker's exit-reason field, including which local operation timed out, without logging command output.
- Default to requiring sign-in, protect detailed health diagnostics, and limit cross-origin browser access to legacy session transfer.
- Isolate the management HTTPS installation tests from the active systemd configuration so they can run on existing HTTPS nodes.
- Guide split-brain preparation by platform and show the next local step. Move network requirements and technical checks into expandable sections.
- Keep new install directories traversable by the service account when the administrator uses a private umask. Recovery backups remain private.
- Choose HTTP or direct HTTPS in the installer for either web server. HTTPS supports a local CA with automatic renewal or an administrator-provided PEM certificate and key.
- Keep reviewed installer settings through sudo, offer usable defaults, and validate management and HA fields as they are entered. Allow fresh installations to opt in to scoped UFW activation.
- Add an explicit uninstall purge for local manager data, dedicated logs, releases and recovery backups after checking that HA has been retired.
- Document runtime dependencies, tested platforms and command-line recovery in English and Dutch. Add a read-only command to compare a protected maintenance checkpoint with the live reservation.
- Report an expired local Proxmox fencing credential even when Pacemaker still reports the shared fencing resource as active.
- Preserve separately installed OS fencing agents, SSH settings and management firewall rules when uninstalling the console.
- Guide witness enrollment, fencing permissions and the migration handoff in Settings. Show whether the local OS recorded the witness enrollment and Corosync key for the reviewed plan.
- Use the live Corosync vote tally with Pacemaker status and mutual TLS evidence, so a stale qdevice vote count after a node rejoins cannot incorrectly mark healthy protection as degraded.
- Show the local migration operation ID and plan fingerprint in Settings while the OS report is fresh, and guide the administrator through creating A or joining B.
- Preserve the scoped firewall rule record until detached protected HA retirement is durable, and safely recognize a repeated retirement request.

## [1.4.0-dev.2] - 2026-11-22

### Pre-release status

This development preview is intended for disposable or recoverable test
deployments. The latest stable release is v1.3.0.

### Verified on Proxmox

- Migrated an existing Keepalived pair and resumed after an interrupted reservation.
- Exercised abrupt failover, fencing during a network partition, and loss of quorum. When neither node had quorum, both released the VIP. Recovery restored one VIP owner.
- Verified rolling application rollback and policy synchronization after a standby returned.

### Changed

- Provide a root-only protected HA preflight that checks the witness CA and client certificate, prepares the shared Corosync key and records authenticated peer power observations before migration.
- Let system administrators save a reviewed Proxmox plan and prepare fixed local cluster dependencies from Settings, with reauthentication and an audited job result.
- Explain initial HA setup and joining in Settings. Show the local, expiring migration phase and the next recovery check to system administrators.
- Allow a protected node to roll back the application release recorded by its latest protected upgrade after the peer takes the VIP. Preserve live policy, accounts and OS cluster configuration.
- Support local retirement and uninstallation after an OS administrator detaches a native HA node and persistently masks its cluster services. Keep pinned management firewall rules.
- Check verified VMware TLS and distinct IPMIv2 fencing endpoints when reading protection evidence from OS-managed clusters.
- Require a verified inactive Keepalived unit and an absent virtual IP before the Pacemaker agent acknowledges a successful stop. Report the failed health check in monitor diagnostics.

- Allow a bounded wait for peer protection before releasing a cluster maintenance reservation. Keep the checkpoint when protection cannot be verified.

- Show a healthy Pacemaker-managed node as standby when its peer serves the protected VIP and local Keepalived is intentionally stopped.

- Open live logs with smaller pages and a bounded recent scan. Keep retained history available on demand, request fewer journal fields and cancel superseded browser requests.
- Show when logs were last fetched and flag potentially stale results. Resume automatic live refresh when the management connection recovers.
- Present grouped client certificate-trust alerts as a short explanation with a count. Preserve individual records for search and the detailed view.

- Gate releases on an isolated Linux-root CI job with no skipped tests, measure application and deployment/tooling coverage, and enforce a coverage baseline.
- Quiet expected disconnects in TLS test servers while making unexpected server-thread errors fail their tests.
- Add visible, translated controls to enlarge documentation screenshots and adjust the zoom in the gallery on desktop and mobile.
- Correct garbled text in the README, documentation, web interface, and installer and account tools.
- Let system administrators validate and download a secret-free Proxmox preparation plan for an existing managed HA pair.
- Explain protected HA prerequisites in Settings and let administrators verify the downloaded plan and fingerprint on the other node.
- Verify the node or witness before staging a protected HA plan and preserve the plan during dependency preparation.
- Add read-only status and a guarded retry for interrupted protected HA activation on Proxmox.
- Recognize Pacemaker's resource refresh timestamp without treating it as an administrator policy change.
- Guard interrupted migration as soon as its checkpoint exists. Require fresh standby evidence before stopping the VIP owner and preserve changed reservation files.
- Make spacing between fields and actions consistent in management password prompts.
- Explain degraded HA protection in Settings, including unconfirmed witness or fencing readiness. Require an explicit cluster-member VIP owner before reporting protection.
- Identify managed HA nodes before sign-in and in Settings, with an optional public label. Prompt for a fresh sign-in when a form is submitted after a node change.
- Keep open management forms in place when the VIP moves, block requests from the stale page and offer an explicit way to reopen the console.

### Known limitations

- Protected HA remains experimental. Settings saves the Proxmox plan and prepares local dependencies. The OS administrator coordinates witness enrollment, fencing access and activation. The browser package workflow was tested in isolation.
- Rolling rollback restores the application release. Full installation recovery and removal of a protected cluster still require OS coordination. Live testing of the full removal procedure remains open.
- Live protected-cluster verification used Proxmox. The documented vSphere, libvirt and physical-server adapters still need live qualification. Full host loss and return after certificate or fencing-token expiry remain open.
- Accounts and sessions remain local. Node B continues to serve as a read-only standby for shared policy.

## [1.4.0-dev.1] - 2026-11-17

### Pre-release status

This development release is intended for disposable or recoverable test deployments.
It does not replace v1.3.0 as the stable release. Management HTTPS and stricter HA
administration are available for testing; protected-cluster provisioning remains
experimental and requires OS administration.

### Changed

- Support numbered development releases with dated notes, numeric version ordering and CI-gated GitHub prerelease publication that preserves the latest stable release.
- Rewrite the Dutch and English roadmap around administrator benefits, released versus unreleased capabilities and remaining product work; keep detailed lab procedures in the verification report.
- Expand the repository README with a capability overview, deployment guidance, a guided first-install path, release/development boundaries and third-party licensing information.

### Added

- Add experimental root-coordinated Proxmox cluster preparation: inert package installation, mandatory client-authenticated witness TLS, encrypted Corosync transport and separately verified activation of a stopped Pacemaker VIP. Retain private operation checkpoints and refuse changed plans, credentials, existing resources or administrator configuration.
- Record live-log performance and clearer certificate-trust warnings as explicit Dutch/English roadmap work, including incremental reads, archive/date searches and preservation of raw records and retention limits.
- Add root-exported, expiring cluster-preparation checks for system administrators, secret-free node/witness plans and separate peer-only Proxmox lab credential delegation. Checks do not activate quorum or imply a completed power-off test.
- Add an experimental opt-in, SHA-256-pinned ClusterLabs Proxmox agent backport with node-local root-only tokens, strict CA/hostname verification and peer-target checks. Keep the distribution agent intact and remove only the exact owned executable during supported uninstall.
- Add system-administrator-only HTTPS setup and renewal in Settings: prepare a certificate, download and acknowledge the node CA, activate HTTPS and renew without handing the web app CA keys or arbitrary root commands. Require fresh authentication, optional 2FA, CSRF and revision checks; record the requesting administrator in privileged audit events.
- Add opt-in direct management HTTPS for Gunicorn and Granian, with a unique node CA, 90-day server certificates, daily renewal checks and a read-only expiry/fingerprint card in settings.
- Add root-operated certificate renewal, verified activation rollback, durable boot recovery and OS-UID audit events. Keep management certificates separate from HA replication trust and leave proxy/VIP services untouched during TLS maintenance.
- Document HTTPS trust, renewal, recovery and explicit HSTS activation in Dutch and English. Preserve managed TLS across upgrades and supported app rollbacks; remove only matching owned TLS units on uninstall.

### Fixed

- Exclude cluster credentials, Corosync keys, NSS databases and private certificate bundles from Git, source archives and installer copies.
- Use threaded Gunicorn workers for direct HTTPS so browser preconnections do not occupy the only two request workers. Verify parallel static requests with idle TCP connections for both supported servers.
- Bound management service shutdown below the TLS helper's timeout and refuse a second web job while the previous root service is still exiting.
- Show a localized reload action when the rule renderer fails to download instead of reporting a secondary JavaScript exception as an API error.
- Correct embedded Gunicorn warning/error severity in journal responses. Group repeated client certificate-trust warnings per minute and hide successful cluster-status housekeeping by default; searching or showing routine messages retains access to individual records.
- Require explicit confirmation, the current administrator password and optional fresh 2FA before revoking HA pairing on either node. Preserve typed input on authentication failure and audit the actor without credentials.
- Reject ambiguous fencing coverage across separate devices, mismatched running agent identities and unverified Proxmox TLS when deriving cluster-protection status.
- Keep public systemd logging directories traversable under the HA setup helper's private umask. Repair only the exact affected installer-owned public Keepalived directory.
- Treat unreadable cluster reservations as uncertain and block independent lifecycle actions, while keeping settings and status pages available.
- Isolate installer self-tests from a host's managed management-TLS profile and transport settings.

### Removed

- Remove obsolete historical documentation under `docs/legacy` and its archive links; current Dutch/English guides and legacy HA compatibility tools remain available.

### Known limitations

- The ordinary HA wizard still configures Keepalived and policy synchronization without quorum or fencing. Experimental protected-cluster setup requires trusted OS coordination; guided GUI provisioning and complete protected-cluster rollback, retirement and removal remain open.
- Controlled Proxmox tests cover real peer power-off/recovery, a frozen active VM, witness loss and unavailable fencing. The full interruption/partition matrix and additional platform adapters are not complete. A witness must have a suitable independent failure domain for production.
- Management HTTPS is opt-in. Clients must trust the node CA. HTTPS by default, broader certificate-provider support and remaining web-hardening checks are still planned.
- Repeated certificate-trust warnings are grouped, but further log-loading performance and presentation improvements remain on the roadmap. Raw records and existing retention controls remain available.
- Accounts and sessions remain local to each node. B stays read-only for shared policy after traffic takeover. The lab fencing-token helper uses expiring credentials and is not a production credential-renewal system.

## [1.3.0] - 2026-11-15

### Added

- Rotate the HA peer key from A's interface with password confirmation, optional fresh TOTP, automatic acknowledgement by B and bounded recovery after a lost response.
- Renew internal HA certificates through an OS-operated staged pin handover, durable commit/recovery records and OS-UID audit attribution.
- Protect HA publication order with membership-scoped generations so B rejects stale, conflicting or downgraded policy publications.
- Add opt-in connection-preserving filter reload for Tinyproxy 1.11.1 and 1.11.3, with fresh journal acknowledgements and native HTTP health checks.
- Show expiring, root-verified failover-protection status without giving the web account access to cluster credentials.
- Add an experimental Pacemaker/Corosync Keepalived resource and guarded rolling app upgrades for OS-prepared two-node clusters, including an exclusive maintenance reservation and explicit recovery.

### Fixed

- Preserve ACLs across native filter updates and recovery, granting Tinyproxy read access only to its own configuration and filter files.
- Reject native filter lines that Tinyproxy would split or interpret differently; never silently restart after a rejected reload or a changed activation plan.
- Persist credential/state replacements and removals before acknowledging changes; redact incomplete peer responses and translate HA dialog errors.
- Keep public certificate-transition metadata outside private TLS directories so existing deployments continue synchronizing without exposing keys.
- Prepare fresh-install backup directories before the service-account import check; refuse backup symlinks and unsafe recursive root migration of application-owned draft files.
- Preserve service-account access when installing from private source directories and isolate installer self-tests from the real host's cluster and certificate state.
- Permit the narrowly scoped peer-key commit endpoint in the legacy internal nginx example.

### Changed

- Publish a bilingual public roadmap with available, opt-in, experimental and planned capabilities, verified checklists and explicit HA limitations.
- Improve documentation screenshots, the lightbox gallery, localized controls and keyboard focus; render command blocks with VS Code Light+ / Dark+ colors.
- Publish stable GitHub Releases automatically from successful tag CI using verified archives, checksums and dated release notes.

### Upgrade notes and limitations

- Upgrade both HA nodes before rotating peer keys or internal certificates. Older internal nginx deployments must allow the peer-restricted `/internal/ha/credential-commit` endpoint; see the administrator guide.
- Normal HA setup still configures Keepalived and automatic policy sync. It does not automatically configure quorum, a witness or fencing; VRRP alone does not guarantee exclusive VIP ownership during a network partition.
- Protected-cluster integration remains experimental. Initial OS provisioning, additional fencing platforms and complete cluster-aware rollback/removal are not yet provided by the GUI or normal installer.
- Reload remains disabled by default. Enable it only on verified supported hosts; other changes require restart and existing tunnels are not retroactively closed by new filter rules.
- Default management HTTPS remains planned. This release preserves existing management listeners; internal HA certificate renewal does not enable HTTPS on the website.

## [1.2.1] - 2026-11-13

### Changed

- Expand the bilingual screenshot guides and give the README a branded introduction.
- Add an accessible screenshot viewer with zoom and a localized GitHub star invitation to the documentation site.
- Improve command blocks and inline code styling in the documentation for both themes and small screens.
- Clarify first sign-in and rule changes for each role in both languages.
- Clarify Dutch operator instructions and explain the proxy manager’s configuration limits in both languages.

### Fixed

- Reject stale filter edits and deletions so a changed line ID cannot modify another administrator's rule. Keep an open editor's revision stable across background refreshes.
- Recheck pending drafts, filter compatibility and revision conflicts under the same cross-process lock as filter writes, including imports.
- Count each skipped import line once, including imports where every valid rule already exists.
- Return client errors for malformed rule requests, invalid import modes and non-boolean mutation options.

## [1.2.0] - 2026-11-12

First official public release. Earlier version headings record development milestones.

### Added

- Add system administrator, proxy manager and monitor roles with server-side permissions, session revocation on role changes and last-administrator protection.
- Pin local management and proxy access with root-owned anti-lockout profiles and narrow, additive UFW exceptions that survive HA policy updates and retirement.
- Add illustrated English/Dutch user guides and a searchable Zensical documentation site with GitHub Pages deployment.

### Changed

- Check Tinyproxy HTTP responsiveness for HA failover and filter identifiable local health probes from the default log view while retaining explicit access to them.
- Publish rule ordering with revision conflict checks and refresh an already-open backup rule list automatically.
- Provide a reviewed allowlist/blocklist switch and a visible, redacted comparison between drafts and active configuration.
- Improve privacy, retention and anti-lockout cards for both themes and small screens; explain NAT management source addresses.
- Refresh bilingual release, upgrade and account-migration instructions; require a strict documentation build before CI release packaging.

### Fixed

- Keep paired backup policy read only with persistent translated guidance and preserve stale-order conflict checks.
- Expire stale service/VIP status and distinguish the active traffic node from the shared-policy writer.
- Preserve unsaved input during connection loss without replaying writes when connectivity returns.
- Keep monitor rule text full width when editing controls are hidden and restrict filter exports to proxy and system administrators.

## [1.1.0] - 2026-11-11

### Added

- Add date-filtered log history across active and archived audit/journal records, bounded queries, stable page anchors, and incomplete-result notices.
- Enforce 30-day management/audit and 7-day Tinyproxy journal retention with isolated namespaces, storage budgets, and hourly cleanup.
- Add bilingual privacy notices, organization contact/policy configuration, and an operator guide for retention and exports.

- Offer detected HA interfaces, local addresses, subnet defaults, and cautious VIP suggestions; check for responding VIP owners before creating a cluster.

- Set up managed HA from existing standalone nodes through reviewed GUI plans and one-time join codes.
- Display authenticator codes as six visual cells while preserving a single accessible input.
- Document logging retention and archive-search findings in both languages.

- Show the last fetched policy revision and check time on the HA standby, and the last standby acknowledgement on the primary.

- Pair managed HA nodes once and reconcile published filters and shared Tinyproxy settings over pinned TLS.

- Add a signed, one-time HA policy transfer for filters and shared Tinyproxy settings.

- Add a disposable VM lab for real installation, proxy traffic, standalone recovery, and two-node HA checks.
- Add a light and dark mode toggle to sign-in and the workspace sidebar.

### Fixed

- Keep destructive button text readable during hover, focus, and active states in both themes.
- Minimize audit payloads, mask IPv4/IPv6 by default, remove duplicate audit journaling and the unused full-rule history writer, and disable HTTP access logging by default.

- Align HA setup fields, avoid DHCP-derived VIP defaults, and discard old standby baselines when creating a new membership.

- Keep HA setup requests locked across service-start timeouts and stop internal replication services before recovery.

- Use the same last successful contact label on both HA nodes.

- Refresh overview rule counts after external filter changes and use singular labels for a count of one.

- Apply HSTS only to HTTPS requests and make subdomain coverage opt-in.

- Preserve sign-in fields when changing language and add a password visibility button.

- Isolate default session cookies between managed HA peers sharing a browser hostname.
- Center the sidebar version and improve dark-mode notification contrast.

- Open Live logs in place so unsaved work and the current page are preserved.

## [1.0.1] - 2026-11-10

### Added

- Add explicit upgrade and uninstall plans for installer-managed deployments, with a recovery snapshot for console removal.
- Preview and validate a two-node HA network plan from the settings page.
- Preview direction-labeled UFW rules and configure them during a fresh HA installation.
- Retire a managed standby HA node from Settings or the installer, with recovery and removal of its recorded UFW rules.

### Fixed

- Keep privileged installer locks and HA recovery state on root-owned paths, and stage HA helper source privately before publishing it.
- Supply the RADIUS attribute dictionary needed for PAP sign-in and verify accepted and rejected requests over UDP.

## [1.0.0] - 2026-11-08

### Added

- Standalone Tinyproxy configuration and filter management, optional Granian server, and installer upgrades with rollback.
- An interactive Linux installer for standalone nodes and two-node Keepalived deployments.
- Individual administrator accounts, optional authenticator-based 2FA with QR enrollment, and audit attribution.
- Dutch/English interface, a shared management console, isolated tests and real HTTP checks.
- Responsive import and live log dialogs, dark mode, and Dutch and English operator guides.
- A single version source, local release preparation and versioned source archives.

### Changed

- Separate core logic, web routes, runtime, deployment assets, development tools and legacy HA components.
- Replace the legacy settings modal with effective proxy configuration and direct account actions.
- Split CI into focused quality, test, frontend, HTTP and release checks.

### Removed

- Retire the original deployment script, setup wizard, pre-production checklist, and legacy Make targets. Use the current installer and administrator guide.

### Fixed

- Preserve a manager binding previously stored only in a systemd unit during upgrades.
- Keep rule text, descriptions and actions aligned; preserve button icons across locales.
- Show localized import examples with real line breaks and refresh the preview when switching import mode.
