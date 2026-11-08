> Legacy-documentatie: beschrijft de oorspronkelijke twee-node-inrichting. Zie de [actuele README](../../README.md).

# Project map and next-step audit

This note records the review baseline before the first stabilization pass. It
is meant as an orientation aid and a practical roadmap, not as a replacement
for the Dutch operator documentation.

## Stabilization status

The first remediation pass after this review fixed the malformed literals,
restricted and authenticated remote merge preview, removed sensitive preview
logging, added a global request-size limit, routed replace/append operations
through the validated atomic writer, removed two unused domain-layer types, and
added initial validator/manager/security tests plus CI import coverage. Findings
below describe the original reviewed state; items not named here remain open.

## What the project is

ProxyPermit is a Linux appliance-style management application.
It provides a single-page Flask UI for editing Tinyproxy's ERE whitelist,
reloading the proxy, inspecting health and logs, and keeping two HA nodes in
sync. Keepalived supplies the virtual IP; a systemd timer runs the independent
sync engine. Optional local or RADIUS authentication and direct or Nginx-
terminated TLS are supported.

The application currently has three overlapping responsibilities:

1. whitelist editor and Tinyproxy service controller;
2. HA observability, conflict resolution, and node-to-node API;
3. host installer/configurator for Tinyproxy, Keepalived, TLS, Nginx, systemd,
   permissions, secrets, and Python.

That breadth is the project's strength, but also the source of most fragility.

## Runtime map

```text
Browser (one 7,016-line HTML/CSS/JS document)
  |
  | session-authenticated JSON API
  v
Flask / Gunicorn (`wsgi.py` -> `app/main.py`)
  |-- `WhitelistManager` -> whitelist file + backups + entry change log
  |-- validators -> domain normalization and Tinyproxy ERE validation
  |-- systemctl -> Tinyproxy/Keepalived status and Tinyproxy reload/restart
  |-- journal/files -> combined UI log viewer
  |-- HMAC sync endpoints -> peer manager
  `-- JSON state files -> session version, sync state, nonces, manual sync

systemd timer -> `/usr/local/bin/sync_whitelist_v2.py`
  |-- reads Keepalived/node configuration
  |-- compares semantic hashes and mtimes
  |-- uploads/downloads through HMAC-authenticated Flask endpoints
  |-- enters or resolves conflict state
  `-- reloads Tinyproxy after applying remote content

Keepalived -> VIP and node role
Tinyproxy  -> consumes `/etc/tinyproxy/whitelist.txt`
```

## Repository map

| Area | Files | Role |
|---|---|---|
| Web backend | `app/main.py` | Flask setup, auth, sync API, logs, status, CRUD, import/export, service control. At 4,427 lines it is the main concentration of risk. |
| Whitelist domain layer | `app/whitelist_manager.py`, `app/validators.py` | Parsing, conversion, validation, backup/write operations, service status. |
| Frontend | `app/templates/index.html`, `app/static/logo.svg` | Complete SPA, styles, modals, peer failover, editor, merge UI, log viewer. It contains about 171 named JS functions in one file. |
| Sync engine | `sync_whitelist_v2.py` | Separate v2.3.0 two-node synchronization process with HMAC, hashes, timestamp/master conflict policy, backups, and reload. |
| Entrypoint | `wsgi.py`, `gunicorn.conf.py` | Gunicorn import and dynamic bind/TLS/worker configuration. |
| Installation | `deploy.sh`, `setup-wizard.py`, `deploy-sync-v2.sh` | Interactive, root-level host mutation and generated configuration. There is substantial overlap between these paths. |
| Host integration | `*.service`, `*.timer`, `proxypermit.logrotate`, `nginx/`, `scripts/` | systemd, log rotation, Nginx rendering, Let's Encrypt, validation, demo, and secret/hash helpers. |
| Operations | `Makefile`, `pre-prod-check.sh` | Convenience commands and production checks; partly duplicate deployment logic. |
| Documentation | `README.md`, `ENVIRONMENT.md`, `BEHEERDERSHANDLEIDING.md`, `WERKINSTRUCTIE.md` | Detailed technical, configuration, administrator, and end-user guidance. |
| CI/tests | `.github/workflows/pylint.yml`, `tests/fixtures/example_whitelist.txt` | Pylint errors only. There are currently no executable automated tests. |

## Data and state

The effective source of truth is still a set of local files rather than a
database. Important defaults are:

- whitelist: `/etc/tinyproxy/whitelist.txt`;
- whitelist backups: `/etc/tinyproxy/.backups/`;
- sync secret: `/etc/proxypermit/sync-secret.key`;
- session version: `/var/lib/proxypermit/session-state.json`;
- sync/conflict state: `/var/lib/proxypermit/sync-state.json`;
- replay nonces: `/var/lib/proxypermit/sync-nonces.json`;
- manual-sync state: `/var/lib/proxypermit/manual-sync.json`;
- audit and entry logs: configurable files, plus systemd journal sources.

This is pleasantly transparent for operators, but every read-modify-write path
must be safe across two Gunicorn workers, the sync timer, and simultaneous UI
requests. It currently is not consistently protected by inter-process locks.

## What is already good

- Domain input is normalized into anchored Tinyproxy ERE patterns and direct
  regex input is screened for unsupported or obviously expensive constructs.
- The normal manager write path validates first, keeps backups, uses a temporary
  file, and restores from backup on failure.
- Sync payloads use HMAC, timestamp and nonce checks, size limits, content hashes,
  semantic hashes, peer IP checks, and explicit conflict state.
- Authentication has hashed-password support, optional RADIUS, session expiry,
  global session invalidation, login throttling, secure-cookie controls, audit
  events, and optional TLS/HSTS/ProxyFix configuration.
- Operational documentation is much more complete than in most projects of this
  size, including separate administrator and end-user views.
- The Windows demo provides a low-friction way to inspect the UI without
  installing the Linux stack.

## Findings, ordered by urgency

### P0: the checked-in backend appears to contain a Python syntax error

`app/whitelist_manager.py` lines 642 and 685 contain malformed string literals
in the nested `norm()` functions (`replace("\r",""")`). A clean import should
fail before the application starts. The deployed instance may therefore differ
from `main`, or the affected code was introduced after that deployment. There is
no Python executable in the current Windows environment, so this was verified by
source inspection rather than an interpreter run. The CI workflow should catch
it, but CI status is not represented in the repository.

### P0: unauthenticated server-side request endpoint

`POST /api/merge/remote-preview` in `app/main.py` is missing `@requires_auth`.
It accepts an arbitrary `remote_url`, appends `/api/sync/download`, makes a
server-side request, permits the caller to disable certificate verification,
and attaches a valid cluster HMAC. It also logs the generated HMAC headers.
This is an SSRF and cluster-credential exposure shape. Restrict the destination
to configured peers, force the configured TLS policy, require manager auth, and
never log authentication material.

### P1: concurrent writes can lose changes or corrupt state

The manager uses read-modify-write operations without an inter-process lock.
Gunicorn defaults to two workers, while the sync process can write the same
whitelist independently. `write_whitelist()` also uses one predictable `.tmp`
path. Nonce, session, sync, entry-log, and manual-sync JSON files generally use
plain read/write cycles without atomic replace or locking. Two requests can
overwrite each other; a crash can leave truncated JSON; two workers can race the
nonce replay check. Use one shared filesystem lock and atomic temp-file replace
helpers for all mutable state, or move coordination/state to SQLite.

### P1: write behavior is inconsistent

`write_whitelist()` is intended to be atomic, although `shutil.move` is less
explicit than `os.replace`. In contrast, `replace_all_entries()` writes the live
whitelist directly and `append_entries()` appends directly. Import and merge
paths can therefore bypass the safest writer. Centralize every whitelist change
through a single validated, locked, atomic transaction followed by one reload.

### P1: no regression safety net

The `tests/` directory contains only example data. CI runs `pylint -E` on Python
3.8-3.10, but does not run unit, API, JavaScript, installation, or shell tests.
The highest-value initial tests are domain/ERE conversion, parse/render
round-trips, atomic write/rollback, CRUD/import behavior, HMAC/replay checks, and
the sync decision matrix. Add one smoke test that imports `wsgi:app`; it would
catch the current syntax issue immediately.

### P1: installation has multiple competing sources of truth

Static systemd units assume `/opt/proxypermit`, while `deploy.sh` and the
Makefile generate units using the checkout directory. The sync unit runs
`/usr/bin/python3` against a copied script, whereas the web app uses the project
venv. `deploy.sh`, `deploy-sync-v2.sh`, the wizard, Makefile, static units, and
Nginx helpers repeat related configuration and cleanup logic. The deploy script
also tries Python 3.13 packages on Ubuntu 24.04 and suppresses that installation
failure, then falls back. This makes upgrades and partial failures difficult to
reason about.

Choose one supported deployment model and generate all artifacts from it. A
versioned Debian package is a natural fit for this appliance; an Ansible role is
a simpler intermediate step. Both are preferable to copying a mutable checkout
and a second copy of the sync script into unrelated paths.

### P2: large modules mix unrelated concerns

`app/main.py` contains security configuration, authentication, persistence,
sync transport, sync orchestration, logging parsers, OS inspection, and all HTTP
controllers. The HTML document combines markup, CSS, state, API client, editor,
merge engine, failover, auth, and log viewer. This makes apparently small changes
high-risk and discourages focused testing.

Split the backend by responsibility (auth, entries, sync, status/logs) using
Flask blueprints and services. Split frontend CSS and JS first without changing
the UI or adding a framework. A build tool is optional; ES modules served as
static files would already be a large improvement.

### P2: duplicate rules can drift

Domain-to-regex and regex-to-domain behavior exists in validators, the manager,
import/export code, and JavaScript. Sync/HMAC request construction and state
handling are likewise duplicated between the Flask app and sync script. The two
copies of the nested backend regex conversion function are already identical
large blocks. Establish Python as the canonical rule engine and test fixtures;
the UI should request conversions/validation from the API or share generated
fixtures rather than independently defining semantics.

### P2: dependency and CI reproducibility

`requests` is unpinned while the other direct dependencies are pinned, and no
lock/hash file records transitive versions. CI uses an old setup-python action,
tests only Python 3.8-3.10, and runs only on push. Define the supported Python and
Ubuntu versions, test the production version, add pull-request runs, and use a
repeatable dependency lock or constraints file with automated update checks.

### P3: likely removable or consolidatable pieces

These are candidates, not deletion instructions:

- `WhitelistEntry` was never instantiated and was removed in the first cleanup.
- `ValidationError` was never used and was removed in the first cleanup.
- checked-in static service files duplicate dynamically generated service files;
- `deploy-sync-v2.sh` duplicates work already performed by `deploy.sh`;
- several Makefile checks duplicate `pre-prod-check.sh`;
- `SYNC_SECRET_PATH` exists only for remote preview while the rest uses
  `SYNC_SECRET_FILE`;
- legacy plaintext `AUTH_PASSWORD` and legacy `MAX_WHITELIST_SIZE` can be given a
  dated removal plan;
- README and administrator guide overlap substantially and should link to one
  canonical installation procedure.

Do not remove the standalone sync service merely because it duplicates some web
sync code: keeping periodic convergence independent from Gunicorn is useful.
Instead, extract a shared sync protocol/policy module used by both entrypoints.

## Recommended direction

The most valuable product direction is not a new feature yet. Make the existing
two-node appliance dependable, repeatable, and recoverable. That turns the
project from a useful bespoke interface into something another operator can
install and trust.

### Milestone 1: stabilization release

1. Fix the syntax error and secure remote preview.
2. Add import/smoke tests and focused unit/API tests.
3. Centralize all whitelist/state writes behind locks and atomic replace.
4. Add optimistic concurrency to mutations (revision/hash in GET, required on
   PUT/DELETE/reorder/import) so stale browser state returns `409 Conflict`.
5. Add request/body size limits and CSRF protection or a deliberate same-origin
   API token model for session-authenticated mutations.
6. Run tests, lint, and shell syntax checks on pull requests.

### Milestone 2: one reliable installer and upgrade path

1. Pick and document one canonical layout and Python version.
2. Make installation non-interactive from a validated config file; retain the
   wizard as a config generator.
3. Generate systemd/Nginx/Keepalived configuration from templates rather than
   embedding copies in several scripts.
4. Add explicit `install`, `upgrade`, `validate`, `backup`, `restore`, and
   `uninstall --purge` semantics. Uninstall should preserve data by default.
5. Test installation and upgrade in a clean Ubuntu 24.04 VM/container in CI.
6. Version the app, config schema, and sync protocol visibly in health output.

### Milestone 3: modularize without redesigning

1. Extract configuration parsing and validation.
2. Extract atomic file/state storage and the whitelist service.
3. Extract the shared sync client, HMAC code, and conflict policy.
4. Move Flask routes into blueprints.
5. Move inline CSS/JS to static modules by feature.
6. Keep API contract tests around the extraction to prevent behavioral drift.

### Milestone 4: useful expansion

After the foundation is stable, the strongest next feature is policy workflow:
staged changes, validation preview, an audit diff, approval, scheduled expiry,
and rollback. That extends the current one-sided editor into a small policy
management system without competing with Tinyproxy itself. A read-only fleet
view for more than two nodes could follow, but multi-node synchronization should
not be generalized until state and conflict semantics are made transactional.

## A practical first slice

A small first pull request can deliver disproportionate value:

- fix the two malformed literals;
- protect and constrain remote preview and remove sensitive header logging;
- add `pytest` with validator tests and `from wsgi import app` smoke coverage;
- add a temporary-directory API test for add/edit/delete/import;
- run those tests on pull requests against the production Python version.

That slice gives a trustworthy baseline. The next pull request can then unify
locked atomic writes, followed by installer consolidation.

## Review limitations

This was a static repository review in a Windows workspace. Python and Bash were
not available here, and no Linux host, Tinyproxy, Keepalived, systemd, peer node,
or browser runtime was exercised. Conclusions about runtime behavior should be
confirmed with a clean Ubuntu installation and a two-node integration test.
