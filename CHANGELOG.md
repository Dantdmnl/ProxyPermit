# Changelog

Historical deployments are not assigned retrospective release tags.

## [Unreleased]

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
