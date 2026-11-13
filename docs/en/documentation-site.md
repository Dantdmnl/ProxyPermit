# Maintain the documentation website

[Nederlands](../nl/documentatiewebsite.md) · [Documentation](README.md)

The website is built with [Zensical](https://zensical.org/). The source remains
ordinary Markdown under `docs/en/` and `docs/nl/`, so it also works on GitHub.
The generated `site/` directory is not committed or installed on proxy nodes.

## Preview locally

Use Python 3.11 or later in a separate virtual environment, plus Node.js 24 or later. From the repository root:

```sh
python -m venv .venv/docs-tools
```

On Linux/macOS:

```sh
.venv/docs-tools/bin/python -m pip install -r requirements/docs.txt
npm ci --prefix scripts/docs --ignore-scripts
.venv/docs-tools/bin/python scripts/docs/site.py serve
```

On Windows PowerShell:

```powershell
.venv/docs-tools/Scripts/python.exe -m pip install -r requirements/docs.txt
npm ci --prefix scripts/docs --ignore-scripts
.venv/docs-tools/Scripts/python.exe scripts/docs/site.py serve
```

Open the local address printed by Zensical. Stop with Ctrl+C. To make the same
validated build as CI, replace `serve` with `build`. Rebuild after editing sources.

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

Store sanitized screenshots in `docs/assets/en/` and `docs/assets/nl/`. Use
consistent example data and capture the relevant panel or dialog. Avoid empty
screens, unnecessary desktop margins and screenshots of terminal text.

Place screenshots beside the task they explain. Use a `figure` with the
`docs-figure` class, a concise `figcaption` and descriptive alt text. Add
`docs-figure--dialog`, `docs-figure--signin` or `docs-figure--mobile` for narrower
captures. Set `loading=lazy` and the original pixel width/height on the Markdown
image to reserve space while it loads. Never stretch a small screenshot.

Zensical's bundled GLightbox supplies the gallery and zoom. Local JavaScript
adds translated labels and focus handling; local CSS styles the figures.
Without JavaScript the image links still open the original file. No external
viewer script, analytics or image service is needed. The GitHub star invitation
appears as a short footer after the page content.

This follows [Zensical's image guidance](https://zensical.org/docs/authoring/images/)
and [Google's screenshot guidance](https://developers.google.com/style/images).

To regenerate the example screenshots, run `tests/browser/settings.cjs` with
`PP_DOC_SCREENSHOTS=1`, Playwright on `NODE_PATH`, and the development virtual
environment ready. The test creates isolated accounts and synthetic log data.
Review the images before committing them; never publish credentials or a 2FA QR code.

With the built site served locally, run `tests/browser/docs.cjs` to check the
image viewer in both languages, themes and screen sizes. Set `PP_DOCS_URL` to
the preview address and `PLAYWRIGHT_CHANNEL` if needed.

The privacy build copies the viewer's runtime assets into the generated site.
A first build needs network access; subsequent builds can reuse the download
cache. Visitors load the viewer from the documentation host. The CI browser job
checks the gallery with external requests blocked, in both languages and themes,
on mobile, tablet and desktop widths, plus original-image links without JavaScript.
Use the preview URL including the `/ProxyPermit/` prefix for those tests.

Code blocks are rendered with Shiki and the VS Code Light+ / Dark+ themes during
the build. There is no client-side highlighter or CDN dependency. Copy buttons
continue to copy the original text, without the language label. The build helper
runs the strict Zensical build and then highlights shell, PowerShell and `.env`
examples; plain text stays unchanged. Use this helper for the final preview and CI.
