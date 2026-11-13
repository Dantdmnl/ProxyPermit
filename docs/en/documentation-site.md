# Maintain the documentation website

[Nederlands](../nl/documentatiewebsite.md) · [Documentation](README.md)

The website is built with [Zensical](https://zensical.org/). The source remains
ordinary Markdown under `docs/en/` and `docs/nl/`, so it also works on GitHub.
The generated `site/` directory is not committed or installed on proxy nodes.

## Preview locally

Use Python 3.11 or later in a separate virtual environment. From the repository root:

```sh
python -m venv .venv/docs-tools
```

On Linux/macOS:

```sh
.venv/docs-tools/bin/python -m pip install -r requirements/docs.txt
.venv/docs-tools/bin/zensical serve
```

On Windows PowerShell:

```powershell
.venv/docs-tools/Scripts/python.exe -m pip install -r requirements/docs.txt
.venv/docs-tools/Scripts/zensical.exe serve
```

Open the local address printed by Zensical. Stop with Ctrl+C. To make the same
validated build as CI, replace `serve` with `build --clean --strict`.

## Enable GitHub Pages

The workflow is prepared in `.github/workflows/docs.yml`. To publish it:

1. Review and push the documentation changes to `main`.
2. In the repository, open **Settings → Pages → Build and deployment**.
3. Choose **GitHub Actions** as the source.
4. Approve public documentation publication by setting the repository Actions
   variable `DOCS_PUBLISH_ENABLED` to `true` under **Settings → Secrets and
   variables → Actions → Variables**. A private source repository does not make
   an ordinary Pages site private.
5. Open **Actions → Documentation → Run workflow** on `main`.
6. Open the published URL from the deployment result after it succeeds.

Subsequent documentation pushes to `main` build and deploy automatically.
Pull requests only build and validate; they cannot deploy the site. The deploy
job alone receives Pages and identity-token write permissions.

The configured address is `https://dantdmnl.github.io/ProxyPermit/`. Update
`site_url` in `zensical.toml` if the repository name or public domain changes.
Pages serves static documentation, not the ProxyPermit console or a demo server.
Content deployed to public Pages is public: use example data and sanitized images.

## Write for the person doing the task

- State who the page is for, what they need and the expected result.
- Use the actual button labels. Explain a new term before using it repeatedly.
- Keep one action per numbered step and say how to check that it worked.
- Explain restarts, replacement and deletion before the confirmation step.
- Keep routine instructions separate from architecture and implementation details.
- Update the matching NL/EN page, language cross-link and navigation together.
- Keep examples free of real accounts, client information and secrets.

The site contains one English/Dutch navigation tree. Interface chrome uses
English; page content and navigation within each language are translated.
The language link at the top of each guide opens its matching translation.
No analytics or remote Google Fonts are configured. Historical documents stay
on GitHub and are excluded from the built site's navigation and search.

## Before merging

Run the strict build, check the changed pages in both themes and on a small
screen, and test navigation, search and the language cross-links. The
[official Zensical publishing guide](https://zensical.org/docs/publish-your-site/)
explains the GitHub Pages integration.

## Screenshots and site interactions

Store sanitized screenshots in `docs/assets/en/` and `docs/assets/nl/` and give
images a descriptive alt text. The site adds a keyboard-accessible image viewer
with zoom; Markdown on GitHub continues to show ordinary images. The logo is not
opened in the viewer. A short, translated GitHub star invitation appears after
the page content, without analytics or third-party scripts.

To regenerate the example screenshots, run `tests/browser/settings.cjs` with
`PP_DOC_SCREENSHOTS=1`, Playwright on `NODE_PATH`, and the development virtual
environment ready. The test creates isolated accounts and synthetic log data.
Review the images before committing them; never publish credentials or a 2FA QR code.

With the built site served locally, run `tests/browser/docs.cjs` to check the
image viewer in both languages, themes and screen sizes. Set `PP_DOCS_URL` to
the preview address and `PLAYWRIGHT_CHANNEL` if needed.
