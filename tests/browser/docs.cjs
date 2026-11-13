// Build and serve site/ first, then run with Playwright installed on NODE_PATH.
// PP_DOCS_URL may include a subpath, e.g. http://127.0.0.1:8000/ProxyPermit/.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

async function main() {
    const base = (process.env.PP_DOCS_URL || 'http://127.0.0.1:8770/').replace(/\/?$/, '/');
    const browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});
    try {
        const page = await browser.newPage();
        for (const language of ['nl', 'en']) {
            for (const theme of ['default', 'slate']) {
                for (const width of [390, 1440]) {
                    await page.setViewportSize({ width, height: 900 });
                    const response = await page.goto(base + language + '/' + (language === 'nl' ? 'werkinstructie' : 'user-guide') + '/');
                    assert.equal(response.status(), 200);
                    await page.evaluate(value => { document.body.dataset.mdColorScheme = value; }, theme);
                    const screenshots = page.locator('.docs-screenshot');
                    assert.ok(await screenshots.count() >= 8);
                    for (const image of await screenshots.locator('img').all()) {
                        assert.equal(await image.evaluate(async element => { await element.decode(); return element.naturalWidth > 0; }), true);
                    }
                    const opener = screenshots.first();
                    await opener.focus();
                    await page.keyboard.press('Enter');
                    const dialog = page.locator('#docs-image-viewer');
                    await dialog.waitFor();
                    await dialog.locator('img').evaluate(element => element.decode());
                    const zoom = dialog.locator('button').first();
                    await zoom.click();
                    assert.equal(await zoom.getAttribute('aria-pressed'), 'true');
                    assert.equal(await dialog.locator('.docs-image-viewport').evaluate(element => element.scrollWidth > element.clientWidth), true);
                    await zoom.click();
                    assert.equal(await zoom.getAttribute('aria-pressed'), 'false');
                    await page.keyboard.press('Escape');
                    await dialog.waitFor({ state: 'hidden' });
                    assert.equal(await opener.evaluate(element => element === document.activeElement), true);
                    assert.equal(await page.locator('body').evaluate(element => element.classList.contains('docs-image-open')), false);
                    await opener.click();
                    await dialog.locator('button').last().click();
                    await dialog.waitFor({ state: 'hidden' });
                    await opener.click();
                    await page.mouse.click(1, 1);
                    await dialog.waitFor({ state: 'hidden' });
                    assert.equal(await page.locator('.docs-support a').getAttribute('href'), 'https://github.com/Dantdmnl/ProxyPermit');
                    assert.match(await page.locator('.docs-support').innerText(), language === 'nl' ? /een ster/ : /a star/i);
                    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                    console.log(`${language} ${theme} ${width}: images, zoom, close controls, keyboard, focus and layout passed`);
                }
            }
        }
    } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
