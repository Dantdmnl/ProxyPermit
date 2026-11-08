# Project layout

[Nederlands](../layout.md) · [Documentation](README.md)

| Path | Contents |
| --- | --- |
| `app/core/` | File locks, validation, accounts, filters, and configuration |
| `app/web/` | Flask pages and API routes |
| `app/runtime/` | Gunicorn and Granian launcher |
| `app/i18n/catalogs/` | Dutch and English text for templates, API messages, and browser code |
| `app/templates/`, `app/static/` | Pages, CSS, JavaScript, and assets |
| `deploy/` | systemd units, Nginx examples, log rotation, and Gunicorn configuration |
| `scripts/deploy/`, `scripts/tools/` | Installer and administration tools |
| `scripts/dev/` | Local test runners, HTTP smoke tests, HA lab, and Windows demo |
| `scripts/check.py`, `scripts/release.py` | Local and CI checks, versioning, and release preparation |
| `requirements/` | Runtime, Granian, and development dependencies |
| `tests/` | Python, JavaScript, Playwright, and fixtures |
| `legacy/`, `docs/legacy/` | Existing HA synchronization tools and historical documentation |

Run commands from the repository root. `bash install.sh` starts the installer.
Use `--plan` for JSON output and run it with `sudo` if existing settings require
root access. Use the [current installer](installation.md) for fresh installs,
upgrades, and recovery. For existing HA clusters with filter synchronization,
`legacy/` contains the sync engine and recovery tools. The corresponding
units are in `deploy/systemd/`.

## Development

Create and activate a Python 3.11+ virtual environment. Install the development
requirements and run the shared checks.

```bash
python -m venv .venv
python -m pip install -r requirements/dev.txt
python scripts/check.py
```

For a direct application import, set `FLASK_SECRET_KEY`, `WHITELIST_PATH`,
`AUDIT_LOG_PATH`, and `SESSION_STATE_FILE`. `TINYPROXY_CONFIG_PATH` selects the
configuration file. `DEPLOYMENT_MODE=standalone` disables peer status and sync
APIs. Existing installations with both node addresses retain HA behavior if the
mode is omitted.

On Windows, run the UI-only demo from PowerShell. It uses local files and does
not start Tinyproxy or apply service changes.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\run-demo-windows.ps1
```

The console uses the browser language initially, with Dutch as the fallback.
The **NL / EN** choice is stored in a cookie for one year. The application keeps
user-entered descriptions, raw configuration, and original log contents in
their original language.
