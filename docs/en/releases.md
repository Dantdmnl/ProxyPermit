# Versions and releases

[Nederlands](../releases.md) · [Documentation](README.md)

Edit `VERSION` to set the source version. The Python and browser version files
are generated. Run `show` to inspect the version, `sync` to update generated
files, and `check` to detect drift.

```bash
python scripts/release.py show
python scripts/release.py sync
python scripts/release.py check
```

Add release notes under `## [Unreleased]` in `CHANGELOG.md`. Preview the
preparation, then apply it once the version and notes are ready.

```bash
python scripts/release.py prepare 2.0.0 --dry-run
python scripts/release.py prepare 2.0.0
python scripts/check.py
python scripts/release.py archive
```

Preparation checks the version and release notes before updating `VERSION`, the
generated files, and the dated changelog section. Dates are UTC. Development
versions can use `MAJOR.MINOR.PATCH-dev.N`. The commands work locally. To create a
stable release tag, use exactly `vVERSION` and include a dated changelog entry.
CI checks both.

The archive command produces `dist/proxypermit-VERSION.tar.gz`, a SHA-256
checksum, and an internal file manifest. The archive contains only distribution
files. CI builds it after checks pass. Publishing and deployment are separate
operations.

## CI

The workflow checks Python, shell syntax, installer plans and typing on Windows
and Linux, action definitions, JavaScript, locale contracts, and rule rendering.
It runs Python unit tests on Linux (3.10, 3.12, 3.14) and Windows (3.14), reports
coverage, exercises HTTP with Gunicorn and Granian, and checks dependencies with
pip-audit. Actions are pinned to commit IDs and the workflow uses read-only
tokens. Dependabot proposes Action and Python dependency updates.

Run the same checks locally from the repository root.

```bash
python -m pip install -r requirements/dev.txt
python scripts/check.py
python scripts/check.py --only frontend version
```

Use Linux or WSL for shell checks and Gunicorn. On Windows, the full command
uses Granian and reports skipped shell checks.
