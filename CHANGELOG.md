# Changelog

Historical deployments are not assigned retrospective release tags.

## [Unreleased]

### Changed

- Publish stable GitHub Releases automatically after successful tag CI, using verified CI artifacts and the tagged changelog.

- Present documentation screenshots as consistently sized, captioned figures with focused example data and reserved image dimensions.
- Replace the custom screenshot dialog with Zensical's bundled lightbox gallery, adding localized controls and keyboard focus handling.
- Self-host gallery assets through the documentation privacy build and check the gallery, focus, sizing and JavaScript-free fallback in CI.
- Move the sign-in illustration to first steps and use a quieter documentation support footer.

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
