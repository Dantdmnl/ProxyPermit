// Build and serve site/ first, then run with Playwright installed on NODE_PATH.
// PP_DOCS_URL may include a subpath, e.g. http://127.0.0.1:8000/ProxyPermit/.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

async function main() {
    const base = (process.env.PP_DOCS_URL || 'http://127.0.0.1:8769/ProxyPermit/').replace(/\/?$/, '/');
    const browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});
    try {
        const page = await browser.newPage();
        page.setDefaultTimeout(10000);
        const externalAssets = [];
        await page.route('**/*', route => {
            if (new URL(route.request().url()).origin === new URL(base).origin) return route.continue();
            if (['script', 'stylesheet', 'font', 'image'].includes(route.request().resourceType())) externalAssets.push(route.request().url());
            return route.abort();
        });
        for (const language of ['nl', 'en']) {
            for (const theme of ['default', 'slate']) {
                for (const width of [390, 1024, 1440]) {
                    await page.setViewportSize({ width, height: 900 });
                    const response = await page.goto(base + language + '/' + (language === 'nl' ? 'werkinstructie' : 'user-guide') + '/');
                    assert.equal(response.status(), 200);
                    await page.evaluate(value => { document.body.dataset.mdColorScheme = value; }, theme);
                    const screenshots = page.locator('figure.docs-figure a.glightbox');
                    assert.equal(await screenshots.count(), 7);
                    assert.equal(await page.locator('figure.docs-figure figcaption').count(), 7);
                    for (const image of await screenshots.locator('img').all()) {
                        assert.equal(await image.evaluate(async element => { element.loading = 'eager'; await element.decode(); return element.naturalWidth > 0; }), true);
                    }
                    const opener = screenshots.first();
                    for (const image of await screenshots.locator('img').all()) {
                        assert.equal(await image.evaluate(element => element.clientWidth <= element.naturalWidth), true, 'Do not upscale screenshots');
                    }
                    await opener.focus();
                    await page.keyboard.press('Enter');
                    const dialog = page.locator('#glightbox-body');
                    await dialog.waitFor();
                    const visibleImage = () => dialog.locator('.current .gslide-image img');
                    await visibleImage().evaluate(element => element.decode());
                    assert.equal(await dialog.getAttribute('aria-modal'), 'true');
                    assert.equal(await dialog.locator('.gclose').getAttribute('aria-label'), language === 'nl' ? 'Sluiten' : 'Close');
                    for (let step = 0; step < 5; step++) {
                        await page.keyboard.press('Tab');
                        assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true);
                    }
                    await page.keyboard.press('ArrowRight');
                    await page.waitForFunction(() => document.querySelector('#glightbox-body .current img')?.src.includes('new-rule.png'));
                    await page.keyboard.press('ArrowLeft');
                    await page.waitForFunction(() => document.querySelector('#glightbox-body .current img')?.src.includes('overview.png'));
                    if (width === 1024) {
                        await visibleImage().click();
                        await page.waitForFunction(() => document.querySelector('#glightbox-body .zoomed'));
                        await visibleImage().click();
                    }
                    await page.keyboard.press('Escape');
                    await dialog.waitFor({ state: 'hidden' });
                    assert.equal(await opener.evaluate(element => element === document.activeElement), true);
                    await opener.click();
                    await dialog.locator('.gclose').click();
                    await dialog.waitFor({ state: 'hidden' });
                    await opener.click();
                    await page.mouse.click(1, 1);
                    if (width <= 768) await dialog.locator('.gclose').click();
                    await dialog.waitFor({ state: 'hidden' });
                    assert.equal(await page.locator('.docs-support a').getAttribute('href'), 'https://github.com/Dantdmnl/ProxyPermit');
                    assert.match(await page.locator('.docs-support').innerText(), language === 'nl' ? /een ster/ : /a star/i);
                    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                    const frame = page.locator('figure.docs-figure a.glightbox').first();
                    assert.equal(await frame.evaluate(element => getComputedStyle(element).borderTopWidth), '0px');
                    assert.equal(await frame.evaluate(element => getComputedStyle(element).paddingTop), '0px');
                    await page.goto(base + language + '/' + (language === 'nl' ? 'installatie' : 'installation') + '/');
                    await page.evaluate(value => { document.body.dataset.mdColorScheme = value; }, theme);
                    const command = page.locator('pre.pp-code-shiki').first();
                    assert.equal(await command.getAttribute('data-language'), 'Bash');
                    assert.equal(await command.locator('code').textContent(), 'sudo bash install.sh\n');
                    const colors = await command.locator('code span[style]').evaluateAll(elements => [...new Set(elements.map(element => getComputedStyle(element).color))]);
                    assert.ok(colors.length >= 2, 'Simple commands must distinguish command names and arguments');
                    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                    console.log(`${language} ${theme} ${width}: figures, gallery navigation, zoom, close controls, keyboard, focus and layout passed`);
                }
            }
        }
        assert.deepEqual(externalAssets, [], 'Viewer assets must be served locally');
        const plain = await browser.newPage({ javaScriptEnabled: false });
        await plain.goto(base + 'en/getting-started/');
        const original = plain.locator('figure.docs-figure a.glightbox').first();
        const response = await plain.request.get(await original.evaluate(link => link.href));
        assert.equal(response.status(), 200);
        assert.match(response.headers()['content-type'], /image\/png/);
        await plain.close();
        console.log('Local viewer assets and original-image links without JavaScript passed.');
    } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
