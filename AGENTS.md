# Working on ProxyPermit

These instructions apply to the entire repository.

## Repository map

- `app/core/` owns filter rules, native Tinyproxy configuration, account data, and file locking. Keep these modules independent of Flask.
- `app/web/` owns Flask routes, authentication, audit logging, and integration with Tinyproxy and systemd. `app/runtime/` selects the WSGI server.
- `app/templates/` contains the HTML. Put styles and browser behavior in `app/static/css/` and `app/static/js/`. Keep Python source, operator logs, and CLI messages in English. Maintain both web catalogs in `app/i18n/catalogs/` and preserve placeholders across languages.
- `install.sh` is the supported installer entry point. `scripts/deploy/` implements installation and upgrades. `deploy/` contains deployment assets. `legacy/` supports synchronization in existing HA deployments.
- `docs/nl/` contains current Dutch documentation, `docs/en/` contains the English equivalents, and `docs/legacy/` contains historical material. Update both current languages when changing operator workflows.

## Operational constraints

- Check standalone behavior and any affected HA paths. New HA installations use Keepalived for the virtual IP. Paired clusters publish shared policy on A and reconcile on B; accounts and host-specific configuration remain local. Existing legacy synchronization has its own compatibility path.
- Keep validation, draft saves, activation, and rollback separate. Preserve existing configuration, file permissions, and revision checks when changing installation or write paths.
- Keep 2FA optional. Preserve authentication, CSRF checks, session revocation, rate limiting, and audit attribution. Use isolated test state for account, proxy, and synchronization changes.
- Logging uses private journal namespaces and locked audit rotation. Preserve the configured 30-day audit/management and 7-day Tinyproxy defaults; never vacuum the default host journal. Check quiet-file expiry, archives, date filters, redaction, and read authorization when changing logging.
- Keep secrets, account files, runtime logs, and local environments out of Git and release archives. Use example values in fixtures.

## Verify and release

- Install `requirements/dev.txt` in a virtual environment and run `python scripts/check.py` from the repository root. To focus on an area, use `--only` with the relevant checks.
- Run `bash install.sh --plan` to inspect installer changes. Use a disposable Linux or WSL environment for installer apply, rollback, or service checks.
- For browser changes, run `tests/browser/settings.cjs` with Playwright available to Node. Cover both languages, both themes, keyboard interaction, and small viewports when the change affects them.
- `VERSION` is the version source. Use `scripts/release.py` for version synchronization and release checks. Record changes under `Unreleased` in `CHANGELOG.md`. Stable tags use `vVERSION` and require a dated changelog section.
