# Prepare and publish a release

[Nederlands](../nl/releases.md) · [Documentation](README.md)

**For:** the maintainer publishing ProxyPermit. Installing or updating a server
is covered by [Installation](installation.md). Preparing files, pushing code,
publishing a release and deploying a server are separate actions.

## 1. Check the documentation and upgrade behavior

Before choosing the release version:

- Update both languages for changed user actions, permissions and installer options.
- Check the [roles](roles.md): existing role-less accounts remain administrators;
  new accounts default to monitor. Monitors cannot export filter rules. Roles,
  passwords and 2FA remain local to each node.
- Check [anti-lockout](administration.md#anti-lockout-profile) with the real
  management source and host-side ports. A NAT source is not the node's own address.
- Verify standalone and paired HA behavior. Compare policy revisions and test
  actual client traffic; a green status alone is insufficient.
- Review [verification](verification.md) and [planned work](planned-work.md).
  Do not describe a planned feature as available in the release.
- Check backup and recovery instructions before asking operators to upgrade.

The documentation website follows `main`, including unreleased work. For a stable
release, its source tag and bundled documentation identify the matching version.
The website is static documentation, not the management console.

## 2. Prepare the version

`VERSION` is the version source; the Python/browser version files are generated
from it. Inspect the current state:

```sh
python scripts/release.py show
python scripts/release.py check
```

Write the changes under **Unreleased** in `CHANGELOG.md`. Choose a new version
that has not been released. The following uses **1.2.0 only as an example**:

```sh
python scripts/release.py prepare 1.2.0 --dry-run
python scripts/release.py prepare 1.2.0
```

Review the diff. Preparation updates `VERSION`, generated files and the dated
changelog section. It stops if the notes or version are invalid. Dates use UTC.
It does not commit, tag, push, publish or deploy anything. Published stable tags
must not be moved to new code; give changed code a new version.

## 3. Validate the candidate

From a Python virtual environment with `requirements/dev.txt` installed:

```sh
python scripts/check.py
python scripts/release.py archive
```

Use Linux/WSL for shell checks and Gunicorn. Windows checks use Granian and
report platform skips. Run `tests/browser/settings.cjs` with Playwright available
to Node for changed browser workflows. Cover NL/EN, both themes and small screens.

Build the documentation with the pinned tools in `requirements/docs.txt` using
`python scripts/docs/site.py build`. The
[documentation-site guide](documentation-site.md#preview-locally) explains setup.

The archive is `dist/proxypermit-VERSION.tar.gz`, accompanied by a SHA-256
checksum. Its internal manifest lists file hashes. Secrets, local environments,
runtime state and the generated website must stay outside the archive.

## 4. Push, check CI, then tag the tested commit

Review and commit the prepared release. Push it through the repository's normal
review process. Wait for **ProxyPermit CI** on the exact commit to succeed.
A local test pass is not proof that the remote workflow succeeded.

Create the release tag on that tested commit. Replace `TESTED_COMMIT_SHA` below
with its actual SHA and use your chosen version:

```sh
git tag -a v1.2.0 TESTED_COMMIT_SHA -m "ProxyPermit v1.2.0"
git push origin v1.2.0
```

A stable tag must be exactly `vVERSION` and have a dated changelog section.
Tag CI verifies both before producing the release archive.

## 5. Publish the download and documentation

Once stable tag CI passes, **Publish stable GitHub Release** calls the reusable
**Publish release** workflow. It publishes the exact **proxypermit-source-release**
archive and checksum from that successful run, with notes from the tagged changelog.
It verifies repository, tag, commit, CI result, archive manifest and checksums.
Existing releases are left unchanged; release assets are never silently replaced.

To publish an older tested tag whose CI did not yet publish releases, run
**Actions → Publish release → Run workflow** on `main`. Enter the stable tag and
its successful **ProxyPermit CI** tag-push run ID. A PR or branch run is rejected.
Expired artifacts require a new successful CI run on that same tag; do not move it.
Only the publication job has release write permission. Actions artifacts expire
after 14 days, so they are temporary build outputs rather than permanent downloads.

Pages deployment is separate. The **Documentation** workflow validates pull
requests and can publish `main` after Pages is configured for GitHub Actions.
See [Enable GitHub Pages](documentation-site.md#enable-github-pages). Verify
navigation, search, images and language links at the deployed project URL.

## What CI checks

`.github/workflows/ci.yml` checks workflow syntax, Python quality and installer
types, version consistency, shell syntax, installer plans, Linux/Windows unit
tests, JavaScript/locales, Playwright flows, real HTTP with Gunicorn/Granian,
dependency vulnerabilities and a strict documentation build. The release job
waits for all these jobs, including documentation.

Actions are pinned to commits and ordinary CI jobs use a read-only token. Coverage is
available as **proxypermit-coverage**. The release publication job receives contents write access; the separate Pages
deployment job receives Pages and identity-token write permissions. Dependabot proposes Action
and Python dependency updates.

## Old workflow runs and source history

The obsolete `.github/workflows/pylint.yml` belongs to the earlier project.
Its historical runs are separate from the current CI and release artifacts.
Deleting an old run removes its Actions entry and associated logs/artifacts;
it does not rewrite commits or revoke access to a previously downloaded copy.

A force push also does not guarantee that old commits disappear from GitHub:
direct SHA links, other references, forks or cached views can remain. Do not
promise that clearing Actions erases historical source. GitHub explains this
in its [repository-history guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Keep current release evidence separate from obsolete workflow cleanup.
