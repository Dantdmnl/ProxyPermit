// Run with Playwright installed on NODE_PATH and the project's Python virtualenv.
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const python = process.env.PP_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const password = 'browser test passphrase';
const secondPassword = 'another browser passphrase';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function fixturePort() {
    const listener = net.createServer();
    await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
    const { port } = listener.address();
    await new Promise(resolve => listener.close(resolve));
    return port;
}

async function checkConnectivity(page, base, signedIn) {
    const context = page.context();
    const routes = signedIn ? ['/', '/proxy', '/settings', '/privacy'] : ['/'];
    for (const language of ['en', 'nl']) {
        for (const route of routes) {
            await page.goto(`${base}${route}?lang=${language}`);
            await page.locator('#connectivityNotice').waitFor({ state: 'attached' });
            for (const theme of ['light', 'dark']) {
                await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
                await page.setViewportSize({ width: 320, height: 568 });
                const input = page.locator('input:not([type=hidden]):not([type=checkbox])').first();
                const editable = await input.count() && await input.isVisible() && await input.isEditable();
                const previous = editable ? await input.inputValue() : null;
                if (editable) await input.fill('offline draft');
                await context.setOffline(true);
                const notice = page.locator('#connectivityNotice');
                await notice.locator('strong').filter({ hasText: language === 'en' ? 'No network connection' : 'Geen netwerkverbinding' }).waitFor();
                assert.equal(await notice.isVisible(), true);
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                if (editable) assert.equal(await input.inputValue(), 'offline draft');
                const url = page.url();
                await page.evaluate(() => [...document.querySelectorAll('a[href]')].find(link => {
                    const url = new URL(link.href);
                    return url.origin === location.origin && (url.pathname !== location.pathname || url.search !== location.search);
                })?.click());
                assert.equal(page.url(), url);
                await context.setOffline(false);
                await notice.locator('[data-connection-dismiss]').waitFor();
                assert.equal(page.url(), url);
                if (editable) {
                    assert.equal(await input.inputValue(), 'offline draft');
                    await input.fill(previous);
                }
                await notice.locator('[data-connection-dismiss]').focus();
                await page.keyboard.press('Enter');
                assert.equal(await notice.isVisible(), false);
            }
        }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${base}/?lang=en`);
    // A server outage with the browser still online needs a different message.
    await page.route('**/connection', route => route.abort('failed'));
    await page.evaluate(() => fetch('/connection').catch(() => {}));
    const notice = page.locator('#connectivityNotice');
    await notice.locator('strong').filter({ hasText: 'Management console unavailable' }).waitFor();
    assert.equal(await page.evaluate(() => navigator.onLine), true);
    await page.unroute('**/connection');
    await notice.locator('[data-connection-retry]').evaluate(button => button.click());
    await notice.locator('[data-connection-dismiss]').click();
    for (const status of [401, 403, 429, 500]) {
        await page.route('**/api/connectivity-read', route => route.fulfill({ status, body: '{}' }));
        await page.evaluate(() => fetch('/api/connectivity-read'));
        await wait(100);
        assert.equal(await notice.isVisible(), false, `An isolated HTTP ${status} is not a connectivity outage`);
        await page.unroute('**/api/connectivity-read');
    }
    await page.route('**/api/validate', route => route.abort('failed'));
    await page.evaluate(() => fetch('/api/validate', { method: 'POST' }).catch(() => {}));
    await wait(200);
    assert.equal(await notice.isVisible(), false, 'A failed validation has not saved any changes');
    await page.unroute('**/api/validate');
    // A lost write response must never lead to an automatic resubmission.
    let mutations = 0;
    await page.route('**/api/connectivity-test', route => { mutations++; return route.abort('failed'); });
    await page.evaluate(() => fetch('/api/connectivity-test', { method: 'POST' }).catch(() => {}));
    await notice.locator('[data-connection-uncertain]').waitFor();
    await wait(200);
    assert.equal(mutations, 1);
    await notice.locator('[data-connection-dismiss]').click();
    await page.unroute('**/api/connectivity-test');
    if (signedIn) {
        await page.getByRole('button', { name: 'New rule' }).click();
        const draft = page.locator('#ruleDialog input[type=text]').first();
        await draft.fill('offline.example');
        await draft.focus();
        await context.setOffline(true);
        await notice.locator('strong').filter({ hasText: 'No network connection' }).waitFor();
        assert.equal(await page.locator('#ruleDialog').evaluate(dialog => dialog.open), true);
        assert.equal(await draft.inputValue(), 'offline.example');
        assert.equal(await draft.evaluate(input => document.activeElement === input), true);
        assert.equal(await notice.evaluate(element => element.matches(':popover-open')), true);
        await context.setOffline(false);
        await notice.locator('[data-connection-dismiss]').click();
        // Leave the fixture draft without publishing it.
        await page.goto(`${base}/?lang=en`);
    }
}

async function checkBackupAndStatus(page, base) {
    for (const endpoint of ['entries', 'status']) await page.route('**/api/' + endpoint, async route => {
        const response = await route.fetch();
        await route.fulfill({ response, json: { ...await response.json(), canEditRules: false,
            ...(endpoint === 'status' ? { current_state: 'MASTER', configured_role: 'BACKUP', vip_active: true, vip_address: '192.0.2.10' } : {}) } });
    });
    let writes = 0;
    const observe = request => { if (/\/api\/entries/.test(request.url()) && request.method() !== 'GET') writes++; };
    page.on('request', observe);
    for (const locale of ['en', 'nl']) {
        await page.goto(base + '/?lang=' + locale);
        await page.locator('#rulesAccessNotice:not([hidden])').waitFor();
        const card = page.locator('.entry-item').first();
        assert.equal(await card.getAttribute('draggable'), 'false');
        await card.locator('.button-danger').click({ force: true });
        await wait(1200);
        assert.equal(await page.locator('#rulesAccessNotice').isVisible(), true);
        assert.match(await page.locator('#rulesAccessNotice').textContent(), /node A/);
        assert.equal(await page.locator('#confirmModal').isVisible().catch(() => false), false);
    }
    assert.equal(writes, 0);
    page.off('request', observe);
    for (const endpoint of ['entries', 'status']) await page.unroute('**/api/' + endpoint);
    await page.goto(base + '/?lang=en');
}

async function checkModeAndCapture(page, base, filterPath) {
    for (const locale of ['en', 'nl']) {
        await page.goto(base + '/proxy?lang=' + locale);
        await page.locator('#filter-policy-mode:not([disabled])').waitFor();
        const original = await page.locator('#config').inputValue();
        await page.locator('#filter-policy-mode').selectOption('allowlist');
        await page.locator('#filter-policy-review').click();
        const dialog = page.locator('#filter-policy-dialog');
        await dialog.waitFor();
        if (process.env.PP_DOC_SCREENSHOTS) {
            const directory = path.join(root, 'docs/assets', locale);
            mkdirSync(directory, { recursive: true });
            await page.setViewportSize({ width: 1360, height: 900 });
            await page.evaluate(() => document.documentElement.dataset.theme = 'light');
            await dialog.screenshot({ animations: 'disabled', path: path.join(directory, 'filter-mode.png') });
        }
        await dialog.locator('button[value=apply]').click();
        assert.equal(await dialog.evaluate(value => value.open), true);
        await page.locator('#filter-policy-confirm').check();
        await dialog.locator('button[value=apply]').click();
        await page.locator('#draft-panel[data-state=dirty]').waitFor();
        assert.match(await page.locator('#config').inputValue(), /FilterDefaultDeny Yes/);
        assert.equal(await page.locator('#draft-panel').getAttribute('data-state'), 'dirty');
        assert.match(await page.locator('#draft-diff').textContent(), /\+ FilterDefaultDeny Yes/);
        if (process.env.PP_DOC_SCREENSHOTS) await page.locator('#draft-panel').screenshot({ animations: 'disabled', path: path.join(root, 'docs/assets', locale, 'draft.png') });
        const saved = await (await page.request.get(base + '/api/proxy/config')).json();
        assert.equal(saved.content, original, 'Mode selection must not save or activate automatically');
        await page.locator('#draft-save').click();
        await page.locator('#draft-panel[data-state=staged]').waitFor();
        assert.match(await page.locator('#draft-diff').textContent(), /\+ FilterDefaultDeny Yes/);
        const persisted = await (await page.request.get(base + '/api/proxy/config')).json();
        assert.equal(persisted.activeContent, original);
        assert.equal(persisted.staged, true);
        page.once('dialog', dialog => dialog.accept());
        await page.locator('#draft-discard').click();
        await page.locator('#draft-panel[data-state=idle]').waitFor();
        await page.locator('#config').evaluate((input, value) => { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); }, original);
        if (process.env.PP_DOC_SCREENSHOTS) {
            const originalRules = readFileSync(filterPath, 'utf8');
            const heading = locale === 'nl' ? 'Bedrijfstoepassingen' : 'Business applications';
            const source = locale === 'nl' ? 'Broncode en samenwerking' : 'Source control and collaboration';
            const packages = locale === 'nl' ? 'Pakketupdates' : 'Package updates';
            writeFileSync(filterPath, `# ${heading}\n^github\\.com(:[0-9]+)?$  # ${source}\n^packages\\.example\\.com(:[0-9]+)?$  # ${packages}\n`);
            await page.goto(base + '/?lang=' + locale);
            await page.locator('.entry-item').first().waitFor();
            await page.locator('#nodeHostname').evaluate(value => value.textContent = 'demo-node');
            await page.screenshot({ fullPage: true, animations: 'disabled', path: path.join(root, 'docs/assets', locale, 'overview.png') });
            await page.locator('#openRuleDialog').click();
            await page.locator('#domainInput').fill('updates.example.com');
            await page.locator('#regexPreview[data-state=valid]').waitFor();
            await page.locator('#ruleDialog').screenshot({ animations: 'disabled', path: path.join(root, 'docs/assets', locale, 'new-rule.png') });
            page.once('dialog', prompt => prompt.accept());
            await page.keyboard.press('Escape');
            const directory = path.join(root, 'docs/assets', locale);
            const signIn = await page.context().browser().newPage({ viewport: { width: 1100, height: 760 } });
            try {
                await signIn.goto(base + '/?lang=' + locale);
                await signIn.locator('#loginForm').waitFor();
                await signIn.evaluate(() => document.documentElement.dataset.theme = 'light');
                await signIn.locator('.signin-card').screenshot({ animations: 'disabled', path: path.join(directory, 'sign-in.png') });
            } finally { await signIn.close(); }
            await page.locator('button[onclick="openImportModal()"]').click();
            await page.locator('#importContent').fill('# Package repository\nupdates.example.com');
            await page.locator('#importButton:not([disabled])').waitFor();
            await page.locator('#importModal .modal-content').screenshot({ animations: 'disabled', path: path.join(directory, 'import.png') });
            page.once('dialog', prompt => prompt.accept());
            await page.keyboard.press('Escape');
            // Use synthetic, stable log data: never publish local audit records.
            await page.route('**/api/logs?*', route => route.fulfill({ json: {
                logs: [
                    { timestamp: '2026-11-13T09:30:00Z', service: 'manager', level: 'INFO', message: 'Tinyproxy configuration reloaded' },
                    { timestamp: '2026-11-13T09:28:00Z', service: 'audit', level: 'INFO', message: 'AUDIT event=whitelist_add, username=demo-admin', audit: { event: 'whitelist_add', username: 'demo-admin' } },
                    { timestamp: '2026-11-13T09:25:00Z', service: 'tinyproxy', level: 'WARNING', message: 'Connection to upstream proxy timed out' }
                ], pagination: { total: 3, offset: 0, hasMore: false }
            } }));
            await page.setViewportSize({ width: 1360, height: 650 });
            await page.locator('#shellNavigation a[href*="logs"]').click();
            await page.locator('#loggingModal .log-row').first().waitFor();
            await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
            await page.locator('#loggingModal .modal-content').screenshot({ animations: 'disabled', path: path.join(directory, 'logs-dark.png') });
            await page.keyboard.press('Escape');
            await page.unroute('**/api/logs?*');
            await page.setViewportSize({ width: 390, height: 844 });
            await page.screenshot({ animations: 'disabled', path: path.join(directory, 'overview-mobile.png') });
            await page.setViewportSize({ width: 1360, height: 900 });
            await page.evaluate(() => document.documentElement.dataset.theme = 'light');
            writeFileSync(filterPath, originalRules);
        }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base + '/?lang=en');
}

async function checkStaleRuleActions(page, base) {
    for (const language of ['en', 'nl']) {
        await page.goto(base + '/?lang=' + language);
        await page.locator('.entry-item').first().waitFor();
        const initial = (await (await page.request.get(base + '/api/entries')).json()).entries[0];
        const replace = async content => {
            const revision = (await (await page.request.get(base + '/api/entries')).json()).revision;
            const response = await page.request.put(base + '/api/entries/0', {
                data: { content, is_comment: true, auto_restart: false, revision }
            });
            assert.equal(response.status(), 200);
        };
        const message = language === 'en' ? 'The rules changed. Refresh the list before making changes.'
            : 'De regels zijn gewijzigd. Ververs de lijst voordat je wijzigingen aanbrengt.';
        try {
            await page.locator('button[onclick="editEntry(0)"]').click();
            await page.locator('#editDescription').fill('My unsaved edit');
            await replace('Another administrator changed this rule');
            // A background refresh must not update the revision of an already-open editor.
            await page.evaluate(() => loadEntries());
            const conflict = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/api/entries/0'));
            await page.locator('#saveEditButton').click();
            assert.equal((await conflict).status(), 409);
            await page.getByText(message, { exact: true }).first().waitFor();
            assert.equal(await page.locator('#editDescription').inputValue(), 'My unsaved edit');
            await page.evaluate(() => closeEditModal(true));
            await page.evaluate(() => loadEntries());
            await page.locator('button[onclick="deleteEntry(0)"]').click();
            await page.locator('#confirmModal.show').waitFor();
            await replace('Keep this newer version');
            await page.evaluate(() => loadEntries());
            const deletion = page.waitForResponse(response => response.request().method() === 'DELETE' && response.url().includes('/api/entries/0?'));
            await page.locator('#confirmOk').click();
            assert.equal((await deletion).status(), 409);
            const current = (await (await page.request.get(base + '/api/entries')).json()).entries[0];
            assert.equal(current.content, 'Keep this newer version');
        } finally {
            await replace(initial.content);
        }
    }
    await page.goto(base + '/?lang=en');
}

async function main() {
    const state = mkdtempSync(path.join(os.tmpdir(), 'proxypermit-playwright-'));
    let processHandle, browser;
    try {
        const port = await fixturePort();
        const base = `http://127.0.0.1:${port}`;
        const file = name => path.join(state, name);
        const accounts = file('accounts.json');
        const filter = file('filter.txt');
        const config = file('tinyproxy.conf');
        const hash = execFileSync(python, ['-c', 'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))'],
                                  { input: password, encoding: 'utf8' }).trim();
        writeFileSync(accounts, JSON.stringify({ admin: { password_hash: hash, revision: 'browser-v1' } }));
        writeFileSync(filter, '# browser fixture\n');
        writeFileSync(config, `Port 3128\nAllow 127.0.0.1\nFilter "${filter.replaceAll('\\', '/')}"\nFilterType ere\n`);
        processHandle = spawn(python, ['-m', 'flask', '--app', 'wsgi:app', 'run', '--port', String(port)], {
            cwd: root, stdio: 'ignore', env: {
                ...process.env, FLASK_SECRET_KEY: 'playwright-isolated-fixture', ENABLE_AUTH: 'true',
                AUTH_ACCOUNTS_FILE: accounts, ENABLE_RADIUS: 'false', SESSION_COOKIE_SECURE: 'false',
                MANAGER_SCHEME: 'http', DEPLOYMENT_MODE: 'standalone', DEMO_MODE: 'true',
                WHITELIST_PATH: filter, TINYPROXY_CONFIG_PATH: config, SESSION_STATE_FILE: file('session.json'),
                NONCE_STORE_FILE: file('nonces.json'), AUDIT_LOG_PATH: file('audit.log'),
                PROXYPERMIT_ACCESS_SAFETY_FILE: file('access-safety.json'), WHITELIST_ENTRY_LOG_PATH: file('entries.json')
            }
        });
        for (let attempt = 0; ; attempt++) {
            try { if ((await fetch(base)).ok) break; } catch { /* Wait for the fixture. */ }
            if (attempt === 49) throw new Error('Fixture did not start');
            await wait(100);
        }
        browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});
        const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
        await checkConnectivity(page, base, false);
        async function checkBrand() {
            const mark = page.locator('.shell-brand img');
            assert.equal(await mark.evaluate(async image => {
                await image.decode();
                return image.naturalWidth > 0 && image.clientWidth === image.clientHeight &&
                    image.src === document.querySelector('link[rel="icon"]').href;
            }), true);
            assert.match((await page.request.get(await page.locator('link[rel="icon"]').evaluate(link => link.href)))
                .headers()['content-type'], /image\/svg\+xml/);
        }
        const openSettings = async () => {
            await page.getByRole('link', { name: 'Settings' }).click();
            await page.getByRole('heading', { name: 'Users and roles' }).waitFor();
        };
        await page.goto(base + '/?lang=en');
        for (const route of ['/', '/proxy', '/settings', '/missing']) {
            const response = await page.request.get(base + route);
            const html = await response.text();
            assert.match(html, /id="loginForm"/);
            assert.doesNotMatch(html, /id="(?:shellNavigation|appConfig|workspace)"/);
            assert.equal(response.headers()['cache-control'], 'no-store');
        }
        await page.setViewportSize({ width: 320, height: 460 });
        await page.emulateMedia({ colorScheme: 'dark' });
        await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
        assert.equal(await page.locator('.signin-card').evaluate(card => getComputedStyle(card).backgroundColor), 'rgb(25, 36, 44)');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        const signInTheme = page.getByRole('button', { name: 'Dark mode' });
        assert.equal(await signInTheme.getAttribute('aria-pressed'), 'true');
        assert.equal(await signInTheme.locator('.theme-sun').isVisible(), true);
        await page.locator('#loginUsername').fill('admin');
        await signInTheme.press('Space');
        assert.equal(await signInTheme.getAttribute('aria-pressed'), 'false');
        assert.equal(await signInTheme.locator('.theme-moon').isVisible(), true);
        assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
        assert.equal(await page.locator('#loginUsername').inputValue(), 'admin');
        await page.locator('#loginPassword').fill('unsent browser password');
        const reveal = page.getByRole('button', { name: 'Show password' });
        await reveal.press('Space');
        assert.equal(await page.locator('#loginPassword').getAttribute('type'), 'text');
        await page.getByRole('link', { name: 'Nederlands' }).click();
        await page.getByRole('heading', { name: 'Inloggen' }).waitFor();
        assert.equal(await page.locator('#loginUsername').inputValue(), 'admin');
        assert.equal(await page.locator('#loginPassword').inputValue(), 'unsent browser password');
        await page.getByRole('button', { name: 'Wachtwoord tonen' }).click();
        assert.equal(await page.locator('#loginPassword').getAttribute('type'), 'password');
        const dutchTheme = page.getByRole('button', { name: 'Donkere modus' });
        assert.equal(await dutchTheme.getAttribute('aria-pressed'), 'false');
        await dutchTheme.click();
        assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
        await page.evaluate(() => localStorage.removeItem('proxypermit-theme'));
        await page.getByRole('link', { name: 'English' }).click();
        await page.getByRole('heading', { name: 'Sign in' }).waitFor();
        assert.equal(await page.locator('#loginPassword').inputValue(), 'unsent browser password');
        await page.locator('#loginPassword').clear();
        const feedback = page.locator('#loginFeedback');
        await page.locator('#loginButton').click();
        await page.keyboard.press('Enter');
        await page.keyboard.press('Enter');
        assert.equal(await feedback.textContent(), 'Enter a username and password');
        await page.locator('#loginUsername').fill('admin');
        await page.mouse.click(4, 4);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#loginUsername').inputValue(), 'admin');
        assert.equal(await feedback.textContent(), '');
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.emulateMedia({ colorScheme: 'light' });
        let loginRequests = 0;
        page.on('request', request => {
            if (request.url().endsWith('/api/auth/login')) loginRequests++;
        });
        let releaseLogin;
        const holdLogin = new Promise(resolve => { releaseLogin = resolve; });
        await page.route('**/api/auth/login', async route => {
            await holdLogin;
            await route.continue();
        }, { times: 1 });
        await page.locator('#loginPassword').fill(password);
        await page.keyboard.press('Enter');
        const dutch = page.getByRole('link', { name: 'Nederlands' });
        try {
            assert.equal(await dutch.getAttribute('aria-disabled'), 'true');
            await dutch.press('Enter');
            assert.equal(await page.locator('html').getAttribute('lang'), 'en');
            assert.equal(await page.locator('#loginButton').isDisabled(), true);
        } finally {
            releaseLogin();
        }
        await page.locator('#workspace').waitFor();
        assert.equal(loginRequests, 1);
        await checkConnectivity(page, base, true);
        await checkBackupAndStatus(page, base);
        await checkModeAndCapture(page, base, filter);
        await checkStaleRuleActions(page, base);
        await checkBrand();
        const remove = page.locator('.entry-item .button-danger').first();
        for (const mode of ['light','dark']) {
            await page.evaluate(value=>{document.documentElement.dataset.theme=value},mode);
            await remove.hover();
            const contrast=await remove.evaluate(button=>{
                const style=getComputedStyle(button);
                const luminance=color=>{
                    const values=color.match(/[0-9.]+/g).slice(0,3).map(Number).map(v=>{
                        v/=255;return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4);
                    });
                    return values[0]*0.2126+values[1]*0.7152+values[2]*0.0722;
                };
                const a=luminance(style.color),b=luminance(style.backgroundColor);
                return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
            });
            assert.ok(contrast>=4.5, `${mode} remove hover contrast: ${contrast}`);
        }
        await page.evaluate(()=>{document.documentElement.dataset.theme='light'});
        await page.mouse.move(0,0);
        assert.equal(await page.locator('.sidebar').evaluate(sidebar => {
            const locale = sidebar.querySelector('.locale-switch').getBoundingClientRect();
            const toggle = sidebar.querySelector('.theme-toggle').getBoundingClientRect();
            const footer = sidebar.querySelector('.sidebar-footer').getBoundingClientRect();
            return Math.abs(locale.left - footer.left) < 1 && locale.right < toggle.left &&
                Math.abs(locale.top + locale.height / 2 - toggle.top - toggle.height / 2) < 1;
        }), true);
        assert.equal(await page.locator('.header-logo').count(), 0);
        assert.equal(await page.locator('.shell-label').first().evaluate(label => getComputedStyle(label).fontSize), '12px');
        const rulesLink = page.getByRole('link', { name: 'Filter rules' });
        await rulesLink.click();
        await page.locator('.shell-nav a[aria-current=location]').waitFor();
        assert.equal(await rulesLink.getAttribute('aria-current'), 'location');
        assert.equal(new URL(page.url()).hash, '#filter-rules');
        await page.getByRole('button', { name: 'New rule' }).click();
        const bounds = await page.locator('#ruleDialog').boundingBox();
        assert.ok(Math.abs(bounds.x + bounds.width / 2 - 720) < 4, JSON.stringify(bounds));
        assert.ok(Math.abs(bounds.y + bounds.height / 2 - 450) < 4, JSON.stringify(bounds));
        await page.keyboard.press('Escape');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'openRuleDialog');
        await page.getByRole('button', { name: 'New rule' }).click();
        await page.locator('#domainInput').fill('not a domain');
        await page.locator('#regexPreview[data-state=error]').waitFor();
        assert.equal(await page.locator('#submitButton').isDisabled(), true);
        await page.locator('#domainInput').fill('example.com');
        await page.locator('#regexPreview[data-state=valid]').waitFor();
        page.once('dialog', prompt => prompt.dismiss());
        await page.mouse.click(5, 5);
        assert.equal(await page.locator('#domainInput').inputValue(), 'example.com');
        assert.equal(await page.locator('#ruleDialog').isVisible(), true);
        await page.locator('#autoRestart').uncheck();
        await page.locator('#submitButton').click();
        await page.locator('#ruleDialog').waitFor({ state: 'hidden' });
        assert.match(readFileSync(filter, 'utf8'), /example/);
        const rulesStat = page.locator('.stat-card').filter({ has: page.locator('#totalEntries') });
        await rulesStat.getByText('Filter rule', { exact: true }).waitFor();
        assert.equal(await page.locator('#totalEntries').textContent(), '1');
        assert.equal(await page.locator('#regexEntries + .label').textContent(), 'Regex pattern');
        assert.equal(await page.locator('#commentEntries + .label').textContent(), 'Comment');
        const savedFilter = readFileSync(filter, 'utf8');
        await page.locator('button[onclick="refreshStatus()"]').click();
        writeFileSync(filter, savedFilter + '\n# external comment\n');
        await page.locator('button[onclick="refreshStatus()"]').click();
        await page.locator('.stat-card').getByText('Comments', { exact: true }).waitFor();
        assert.equal(await page.locator('#totalEntries').textContent(), '1');
        writeFileSync(filter, savedFilter);
        await page.locator('button[onclick="refreshStatus()"]').click();
        await page.locator('.stat-card').getByText('Comment', { exact: true }).waitFor();
        await page.getByRole('button', { name: 'Collapse navigation' }).click();
        assert.equal(await page.locator('.sidebar').evaluate(sidebar => {
            const rail = sidebar.getBoundingClientRect();
            const theme = sidebar.querySelector('.theme-toggle').getBoundingClientRect();
            return Math.abs(theme.x + theme.width / 2 - rail.x - rail.width / 2) < 1 &&
                [...sidebar.querySelectorAll('.shell-brand, .shell-toggle, .shell-nav a')].every(control => {
                const box = control.getBoundingClientRect();
                return box.width === 40 && box.height === 40 && Math.abs(box.left + box.width / 2 - rail.left - rail.width / 2) < 1;
            });
        }), true);
        await openSettings();
        await page.route('**/api/settings/ha/network', route => route.fulfill({json:{networks:[
            {interface:'ens18',address:'192.0.2.11',prefix:24,network:'192.0.2.0/24',dynamic:false,suggestedVip:'192.0.2.10/24'},
            {interface:'ens19',address:'198.51.100.11',prefix:24,network:'198.51.100.0/24',dynamic:false,suggestedVip:'198.51.100.10/24'},
            {interface:'enp0s3',address:'10.0.2.15',prefix:24,network:'10.0.2.0/24',dynamic:true,suggestedVip:'10.0.2.10/24'}
        ]}}));
        await page.getByText('Validate HA settings', { exact: true }).click();
        const haForm = page.locator('#haPreviewForm');
        await page.locator('#haDetectedNetwork option[value="0"]').waitFor({state:'attached'});
        await page.locator('#haDetectedNetwork').selectOption('0');
        assert.equal(await haForm.locator('[name=interface]').inputValue(), 'ens18');
        assert.equal(await haForm.locator('[name=nodeA]').inputValue(), '192.0.2.11');
        assert.equal(await haForm.locator('[name=clients]').inputValue(), '192.0.2.0/24');
        assert.equal(await haForm.locator('[name=vip]').inputValue(), '192.0.2.10/24');
        await haForm.locator('[name=clients]').fill('192.0.2.128/25');
        await page.locator('#haDetectedNetwork').selectOption('1');
        assert.equal(await haForm.locator('[name=clients]').inputValue(), '192.0.2.128/25');
        await page.locator('#haDetectedNetwork').selectOption('0');
        await page.locator('#haDetectedNetwork').selectOption('2');
        assert.equal(await haForm.locator('[name=vip]').inputValue(), '');
        assert.equal(await haForm.getByRole('button', {name:'Validate HA plan'}).isDisabled(), true);
        await page.locator('#haDetectedNetwork').selectOption('0');
        assert.equal(await haForm.getByRole('button', {name:'Validate HA plan'}).isDisabled(), false);
        assert.equal(await haForm.evaluate(form => {
            for (const [left,right] of [['node','interface'],['nodeA','nodeB'],['vip','clients']]) {
                if (Math.abs(form.elements[left].getBoundingClientRect().top-form.elements[right].getBoundingClientRect().top)>1) return false;
            }
            return true;
        }), true);
        await haForm.getByLabel('Node', { exact: true }).selectOption('b');
        await haForm.getByLabel('Network interface').fill('ens18');
        await haForm.getByLabel('IPv4 address of node A').fill('192.0.2.11');
        await haForm.getByLabel('IPv4 address of node B').fill('192.0.2.12');
        await haForm.getByLabel('Virtual IPv4 address with prefix').fill('192.0.2.10/24');
        await haForm.getByLabel('Allowed client network with prefix').fill('192.0.2.0/24');
        await haForm.getByRole('button', { name: 'Validate HA plan' }).click();
        const haResult = page.locator('#haPreviewResult');
        await haResult.waitFor();
        assert.match(await haResult.textContent(), /--plan --mode ha --ha-node b/);
        await page.getByText('UFW rules for this node', { exact: true }).click();
        const firewall = await page.locator('#haFirewallRules').textContent();
        assert.match(firewall, /allow in on ens18 from 192\.0\.2\.11 to 192\.0\.2\.12 proto vrrp comment 'ProxyPermit HA IN/);
        assert.match(firewall, /allow out on ens18 from 192\.0\.2\.12 to 192\.0\.2\.11 proto vrrp comment 'ProxyPermit HA OUT/);
        assert.equal(await page.locator('.shell-toggle').getAttribute('aria-expanded'), 'false');
        const theme = page.getByRole('combobox', { name: 'Theme' });
        await page.emulateMedia({ colorScheme: 'dark' });
        await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
        assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
        await theme.selectOption('light');
        assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
        await theme.selectOption('dark');
        const shellTheme = page.getByRole('button', { name: 'Dark mode' });
        await shellTheme.click();
        assert.equal(await theme.inputValue(), 'light');
        await shellTheme.click();
        assert.equal(await theme.inputValue(), 'dark');
        const settingsUrl = page.url();
        await page.getByRole('link', { name: 'Live logs' }).click();
        await page.locator('#loggingModal').waitFor();
        await page.keyboard.press('Escape');
        assert.equal(page.url(), settingsUrl);
        await page.getByRole('link', { name: 'Proxy configuration' }).click();
        await checkBrand();
        assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
        assert.equal(await page.locator('.change-bar').evaluate(bar => getComputedStyle(bar).backgroundColor), 'rgb(25, 36, 44)');
        const logsLink = page.getByRole('link', { name: 'Live logs' });
        async function checkDialogLayout(dialog) {
            const layout = await dialog.evaluate(element => {
                const header = element.querySelector('header').getBoundingClientRect();
                const close = element.querySelector('.modal-close-btn').getBoundingClientRect();
                const overflow = [...element.querySelectorAll('.workspace-panel, .workspace-dialog-body, .log-container')]
                    .filter(part => part.scrollWidth > part.clientWidth + 1)
                    .map(part => part.className);
                return { overflow, inset: header.right - close.right, closeWidth: close.width, closeHeight: close.height };
            });
            assert.deepEqual(layout.overflow, []);
            assert.ok(layout.inset >= 10 && layout.closeWidth >= 36 && layout.closeHeight >= 36, JSON.stringify(layout));
        }
        const proxyUrl = page.url();
        await logsLink.click();
        await page.locator('#loggingModal .modal-header h3').waitFor();
        assert.equal(page.url(), proxyUrl);
        await checkDialogLayout(page.locator('#loggingModal'));
        const lowContrastTitles = await page.locator('.modal-header h3, .rule-dialog-header h2').evaluateAll(headings => {
            function luminance(color) {
                const channels = color.match(/[\d.]+/g).slice(0, 3).map(value => Number(value) / 255);
                return channels.reduce((sum, channel, index) => sum + [0.2126, 0.7152, 0.0722][index] *
                    (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4), 0);
            }
            return headings.filter(heading => {
                const text = luminance(getComputedStyle(heading).color);
                const panel = heading.closest('.modal-content, .rule-dialog');
                const surface = luminance(getComputedStyle(panel).backgroundColor);
                return (Math.max(text, surface) + 0.05) / (Math.min(text, surface) + 0.05) < 4.5;
            }).map(heading => heading.textContent.trim());
        });
        assert.deepEqual(lowContrastTitles, []);
        await page.locator('#loggingModal .modal-close-btn').click();
        await page.waitForFunction(() => window.location.hash === '');
        const logRequests = [];
        let logFailure = false;
        let routineLogs = false;
        const logEndpoint = /\/api\/logs(?:\?|$)/;
        await page.route(logEndpoint, async route => {
            const url = new URL(route.request().url());
            logRequests.push(url);
            if (logFailure) {
                await route.fulfill({ status: 503, json: { error: 'Fixture log failure' } });
                return;
            }
            if (routineLogs) {
                const timestamp = 'Nov 08 22:18:15';
                await route.fulfill({ json: { logs: [
                    { timestamp, service: 'tinyproxy', level: 'INFO', message: 'Reloading config file finished' },
                    { timestamp, service: 'tinyproxy', level: 'INFO', message: 'Initializing tinyproxy ...' },
                    { timestamp, service: 'tinyproxy', level: 'WARNING', message: 'FLAGS is unset' },
                    { timestamp, service: 'audit', level: 'INFO', message: 'AUDIT event=whitelist_add, username=admin',
                        audit: { event: 'whitelist_add', username: 'admin' } }
                ] } });
                return;
            }
            await route.fulfill({ json: {
                logs: [{ timestamp: '<img src=x>', service: 'manager', level: 'INFO',
                    message: 'literal <img src=x> [proxy.example]', meta: { client_name: 'node" onclick="injected' } }],
                pagination: { total: 1, offset: 0, hasMore: false }
            } });
        });
        await logsLink.click();
        const logs = page.locator('#loggingModal');
        await logs.getByText('literal <img src=x> [proxy.example]', { exact: true }).waitFor();
        await page.setViewportSize({ width: 1178, height: 856 });
        await page.locator('html').evaluate(html => { html.dataset.theme = 'light'; });
        assert.equal(await logs.locator('#logContainer').evaluate(pane => getComputedStyle(pane).backgroundColor), 'rgb(243, 246, 248)');
        await checkDialogLayout(logs);
        await page.locator('html').evaluate(html => { html.dataset.theme = 'dark'; });
        assert.equal(await logs.locator('img, [onclick="injected"]').count(), 0);
        assert.equal(await logs.locator('.log-chip').getAttribute('data-filter-value'), 'node" onclick="injected');
        const search = logs.locator('#logFilter');
        await search.fill('[proxy.example]');
        await logs.locator('mark').filter({ hasText: '[proxy.example]' }).waitFor();
        assert.ok(logRequests.every(url => !url.searchParams.has('q')));
        await search.press('Enter');
        await logs.getByText('1 result for “[proxy.example]”').waitFor();
        assert.equal(logRequests.at(-1).searchParams.get('q'), '[proxy.example]');
        await logs.locator('#logLiveBanner').click();
        assert.equal(await search.inputValue(), '');
        await logs.locator('#autoRefreshBtn').click();
        logFailure = true;
        await logs.getByRole('button', { name: 'Refresh now' }).click();
        await logs.getByText('Fixture log failure').waitFor();
        assert.equal(await logs.locator('.log-row').count(), 1);
        logFailure = false;
        await logs.getByRole('button', { name: 'Refresh now' }).click();
        await logs.locator('#logLoadingStatusText').filter({ hasText: 'Done' }).waitFor();
        routineLogs = true;
        await logs.getByRole('button', { name: 'Refresh now' }).click();
        await logs.getByText('Tinyproxy · 2 routine messages').click();
        await logs.getByText('Initializing tinyproxy ...').waitFor();
        await logs.getByText('FLAGS is unset').waitFor();
        await logs.getByText('Filter rule added by admin').click();
        await logs.getByText('AUDIT event=whitelist_add, username=admin').waitFor();
        await logs.locator('.log-period > summary').click();
        await logs.locator('#logSince').fill('2026-11-10T10:00:00');
        await logs.locator('#logUntil').fill('2026-11-10T11:00:00');
        const datedRequest=page.waitForRequest(request=>request.url().includes('/api/logs?') && request.url().includes('since='));
        await logs.getByRole('button',{name:'Apply date range'}).click();
        const range=new URL((await datedRequest).url());
        assert.match(range.searchParams.get('since'), /Z$/);
        assert.match(range.searchParams.get('until'), /Z$/);
        assert.equal(await logs.locator('#autoRefreshBtn').isDisabled(), true);
        await logs.locator('#logClearPeriod').click();
        assert.equal(await logs.locator('#logSince').inputValue(),'');
        assert.equal(await logs.locator('#logUntil').inputValue(),'');
        await logs.locator('.log-period > summary').click();

        await page.setViewportSize({ width: 390, height: 700 });
        await checkDialogLayout(logs);
        assert.ok(await logs.locator('#logContainer').evaluate(pane => pane.clientHeight >= 100 && pane.scrollWidth <= pane.clientWidth));
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.keyboard.press('Escape');
        await logs.waitFor({ state: 'hidden' });
        assert.equal(page.url(), proxyUrl);
        await page.unroute(logEndpoint);
        const auditResponse = await page.request.get(base + '/api/logs?service=audit&q=whitelist_add');
        assert.equal(auditResponse.status(), 200);
        const auditLogs = (await auditResponse.json()).logs;
        assert.ok(auditLogs.some(log => log.audit?.event === 'whitelist_add' && log.audit.username === 'admin' &&
            log.message.includes('event=whitelist_add')));
        await page.getByRole('link', { name: 'Overview', exact: true }).click();
        await page.getByRole('button', { name: 'New rule' }).click();
        assert.equal(await page.locator('#ruleDialog').evaluate(dialog => getComputedStyle(dialog).backgroundColor), 'rgb(25, 36, 44)');
        await page.keyboard.press('Escape');
        await page.locator('button[onclick="openImportModal()"]').click();
        const importDialog = page.locator('#importModal');
        assert.equal(await importDialog.locator('.modal-content').evaluate(panel => getComputedStyle(panel).backgroundColor), 'rgb(25, 36, 44)');
        await page.setViewportSize({ width: 811, height: 529 });
        await page.locator('html').evaluate(html => { html.dataset.theme = 'light'; });
        await checkDialogLayout(importDialog);
        await page.locator('html').evaluate(html => { html.dataset.theme = 'dark'; });
        await page.setViewportSize({ width: 2560, height: 1440 });
        await importDialog.locator('#importContent').fill(Array.from({ length: 240 }, (_, index) => `import${index}.example`).join('\n'));
        await importDialog.locator('#importPreviewToggle').click();
        const largeImport = await importDialog.boundingBox();
        assert.ok(largeImport.width <= 640 && largeImport.height <= 580, JSON.stringify(largeImport));
        await checkDialogLayout(importDialog);
        await page.setViewportSize({ width: 811, height: 529 });
        await page.locator('#importContent').fill('draft.example');
        page.once('dialog', prompt => prompt.dismiss());
        await page.mouse.click(5, 5);
        assert.equal(await page.locator('#importContent').inputValue(), 'draft.example');
        page.once('dialog', prompt => prompt.accept());
        await page.keyboard.press('Escape');
        await importDialog.waitFor({ state: 'hidden' });
        await page.setViewportSize({ width: 390, height: 640 });
        await page.locator('button[onclick="openImportModal()"]').click();
        await checkDialogLayout(importDialog);
        assert.equal(await importDialog.locator('#importMode').inputValue(), 'append');
        assert.equal(await importDialog.locator('#importButton').isDisabled(), true);
        await importDialog.locator('#importFile').setInputFiles({
            name: 'filter.txt', mimeType: 'text/plain', buffer: Buffer.from('file-import.example\n')
        });
        await importDialog.locator('#importPreview').getByText('Expected changes').waitFor();
        await importDialog.locator('#importMode').selectOption('replace');
        assert.equal(await importDialog.locator('#importButton').isDisabled(), true);
        await importDialog.locator('#importConfirmReplace').check();
        assert.equal(await importDialog.locator('#importButton').isEnabled(), true);
        const importFooter = await importDialog.locator('footer').boundingBox();
        assert.ok(importFooter.y + importFooter.height <= 640);
        await importDialog.locator('#importMode').selectOption('append');
        await importDialog.locator('#importAutoRestart').uncheck();
        await importDialog.locator('#importButton').click();
        await importDialog.locator('#importFeedback[data-state=success]').waitFor();
        assert.ok(readFileSync(filter, 'utf8').includes('file\\-import\\.example'));
        assert.equal(await importDialog.locator('#importButton').isDisabled(), true);
        await page.keyboard.press('Escape');
        await importDialog.waitFor({ state: 'hidden' });
        await page.route('**/api/import', route => route.fulfill({ status: 400, json: {
            success: false, error: 'No valid rules', errors: ['Line 1: invalid rule']
        } }), { times: 1 });
        await page.locator('button[onclick="openImportModal()"]').click();
        await importDialog.locator('#importContent').fill('invalid import input');
        await importDialog.locator('#importButton').click();
        await importDialog.getByText('No valid rules').waitFor();
        await importDialog.getByText('View import errors').click();
        await importDialog.getByText('Line 1: invalid rule').waitFor();
        assert.equal(await importDialog.locator('#importContent').inputValue(), 'invalid import input');
        page.once('dialog', prompt => prompt.accept());
        await page.keyboard.press('Escape');
        await importDialog.waitFor({ state: 'hidden' });
        await page.setViewportSize({ width: 1440, height: 900 });
        await openSettings();
        await checkBrand();
        assert.equal(await theme.inputValue(), 'dark');
        assert.equal((await page.request.post(base + '/api/settings/accounts', { data: {} })).status(), 403);
        await page.getByRole('button', { name: 'Add account' }).click();
        await page.locator('#accountName').fill('reviewer');
        await page.locator('#newAccountPassword').fill(secondPassword);
        await page.locator('#currentAccountPassword').fill(password);
        await page.locator('#accountSubmit').click();
        await page.getByText('reviewer', { exact: true }).waitFor();
        const other = await browser.newContext();
        assert.equal((await other.request.post(base + '/api/auth/login', { data: { username: 'reviewer', password: secondPassword } })).status(), 200);
        const monitorPage = await other.newPage();
        await monitorPage.goto(base + '/?lang=en');
        await monitorPage.locator('#rulesAccessNotice:not([hidden])').waitFor();
        assert.match(await monitorPage.locator('#rulesAccessNotice').textContent(), /monitor role/);
        assert.equal(await monitorPage.locator('#openRuleDialog').isVisible(), false);
        assert.equal(await monitorPage.locator('.shell-nav a[href="/proxy"]').count(), 0);
        assert.equal(await monitorPage.locator('.entry-item[draggable="true"]').count(), 0);
        for (const locale of ['en', 'nl']) {
            await monitorPage.goto(base + '/settings?lang=' + locale);
            for (const mode of ['light', 'dark']) {
                await monitorPage.locator('#themePreference').selectOption(mode);
                await monitorPage.setViewportSize({ width: 390, height: 844 });
                assert.equal(await monitorPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                assert.equal(await monitorPage.locator('[data-action="password"]').isVisible(), true);
                assert.equal(await monitorPage.locator('[data-action="totp-start"]').isVisible(), true);
                assert.equal(await monitorPage.locator('#accounts-title').count(), 0);
                assert.equal(await monitorPage.locator('#haPreviewForm').count(), 0);
                await monitorPage.goto(base + '/?lang=' + locale);
                await monitorPage.locator('.rule-card .rule-body').first().waitFor();
                assert.equal(await monitorPage.locator('button[onclick="openExportModal()"]').isVisible(), false);
                assert.equal((await other.request.get(base + '/api/export')).status(), 403);
                for (const width of [320, 390, 1440]) {
                    await monitorPage.setViewportSize({ width, height: 900 });
                    const geometry = await monitorPage.locator('.rule-card').first().evaluate(card => ({
                        card: card.getBoundingClientRect().width,
                        text: card.querySelector('.rule-body').getBoundingClientRect().width,
                        overflow: document.documentElement.scrollWidth > innerWidth
                    }));
                    assert.ok(geometry.text >= geometry.card - 26, JSON.stringify({ locale, mode, width, ...geometry }));
                    assert.equal(geometry.overflow, false);
                }
                await monitorPage.goto(base + '/settings?lang=' + locale);

            }
        }
        await page.getByRole('button', { name: 'Change role reviewer' }).click();
        await page.locator('#accountRole').selectOption('manager');
        await page.locator('#currentAccountPassword').fill(password);
        await page.locator('#accountSubmit').click();
        await page.locator('[data-action=role][data-username=reviewer][data-role=manager]').waitFor();
        assert.equal((await other.request.get(base + '/api/config')).status(), 401);
        assert.equal((await other.request.post(base + '/api/auth/login', { data: { username: 'reviewer', password: secondPassword } })).status(), 200);
        await monitorPage.goto(base + '/proxy?lang=en');
        await monitorPage.locator('#validate:not([disabled])').waitFor();
        assert.equal((await other.request.get(base + '/api/proxy/config')).status(), 200);
        assert.equal((await other.request.post(base + '/api/auth/force-logout', { data: {} })).status(), 403);
        await monitorPage.close();
        await page.getByRole('button', { name: 'Remove account reviewer' }).click();
        await page.locator('#currentAccountPassword').fill(password);
        await page.locator('#accountSubmit').click();
        await page.getByRole('list').getByText('reviewer', { exact: true }).waitFor({ state: 'detached' });
        assert.equal((await other.request.get(base + '/api/config')).status(), 401);
        await page.setViewportSize({ width: 390, height: 700 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        const mobileNav = page.locator('.sidebar');
        const mobileNavToggle = mobileNav.locator('.shell-toggle');
        assert.equal(await mobileNavToggle.getAttribute('aria-expanded'), 'false');
        const collapsedHeight = (await mobileNav.boundingBox()).height;
        assert.equal(await mobileNav.evaluate(sidebar => {
            const navigation = sidebar.querySelector('.shell-toggle').getBoundingClientRect();
            const theme = sidebar.querySelector('.theme-toggle').getBoundingClientRect();
            return Math.abs(navigation.top - theme.top) < 1 && navigation.right < theme.left;
        }), true);
        await mobileNavToggle.click();
        assert.equal(await mobileNav.locator('.locale-switch').isVisible(), true);
        await mobileNavToggle.click();
        assert.equal((await mobileNav.boundingBox()).height, collapsedHeight);
        await page.getByRole('button', { name: 'Set up 2FA' }).click();
        await page.locator('#currentAccountPassword').fill(password);
        await page.locator('#accountSubmit').click();
        await page.locator('#enrollmentSecret').waitFor();
        assert.equal(await page.locator('#enrollmentQr').evaluate(async image => {
            await image.decode();
            return image.naturalWidth > 100 && image.src.startsWith('data:image/png;base64,');
        }), true);
        const secret = await page.locator('#enrollmentSecret').inputValue();
        const code = execFileSync(python, ['-c', 'import sys,pyotp; print(pyotp.TOTP(sys.stdin.read()).now())'],
                                  { input: secret, encoding: 'utf8' }).trim();
        await page.locator('#accountOtp').fill(code);
        await page.locator('#accountSubmit').click();
        await page.getByRole('button', { name: 'Disable 2FA' }).waitFor();
        const signin = await other.newPage();
        await signin.goto(base + '/proxy?lang=en');
        await signin.locator('#loginUsername').fill('admin');
        await signin.locator('#loginPassword').fill(password);
        await signin.locator('#loginButton').click();
        await signin.locator('#loginOtp').waitFor();
        assert.equal(await signin.evaluate(() => document.activeElement.id), 'loginOtp');
        await signin.locator('#loginOtp').evaluate(input => {
            const clipboardData = new DataTransfer();
            clipboardData.setData('text/plain', '123 456');
            input.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }));
        });
        assert.equal(await signin.locator('#loginOtp').inputValue(), '123456');
        assert.deepEqual(await signin.locator('.signin-code-cells span').allTextContents(), ['1', '2', '3', '4', '5', '6']);
        await signin.keyboard.press('ArrowLeft');
        assert.equal(await signin.locator('.signin-code-cells [data-current]').count(), 1);
        await signin.locator('#loginOtp').fill(code);
        await signin.locator('#loginButton').click();
        await signin.locator('#loginFeedback').filter({ hasText: /.+/ }).waitFor();
        assert.equal((await other.request.post(base + '/api/auth/login', { data: { username: 'admin', password, otp: code } })).status(), 401);
        // The adjacent time step is accepted and has not been consumed by enrollment.
        const freshCode = execFileSync(python, ['-c', 'import sys,time,pyotp; print(pyotp.TOTP(sys.stdin.read()).at(time.time() + 30))'],
                                       { input: secret, encoding: 'utf8' }).trim();
        await signin.locator('#loginOtp').fill(freshCode);
        await signin.locator('#loginButton').click();
        await signin.locator('#workspace').waitFor();
        assert.equal(new URL(signin.url()).pathname, '/proxy');
        const events = readFileSync(file('audit.log'), 'utf8').split('\n').filter(Boolean).map(JSON.parse);
        assert.ok(events.some(event => event.username === 'admin' && event.event === 'account_create' && event.target_account === 'reviewer'));
        assert.ok(events.some(event => event.username === 'admin' && event.event === 'account_totp_confirm'));
        for (const viewport of [
            { width: 320, height: 568 }, { width: 390, height: 844 },
            { width: 768, height: 1024 }, { width: 844, height: 390 },
            { width: 1440, height: 560 }, { width: 2560, height: 1440 }
        ]) {
            await page.setViewportSize(viewport);
            for (const route of ['/', '/proxy', '/settings']) {
                await page.goto(base + route);
                await page.locator('#workspace').waitFor();
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
                             `Horizontal overflow on ${route} at ${viewport.width}×${viewport.height}`);
            }
            await page.goto(base);
            await page.getByRole('button', { name: 'New rule' }).click();
            const footer = await page.locator('#ruleDialog .rule-dialog-footer').boundingBox();
            assert.ok(footer && footer.y >= 0 && footer.y + footer.height <= viewport.height,
                      `Dialog actions clipped at ${viewport.width}×${viewport.height}`);
            await page.keyboard.press('Escape');
        }
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.getByRole('button', { name: 'Sign out' }).click();
        await page.locator('#loginForm').waitFor();
        assert.equal(await page.locator('#shellNavigation').count(), 0);
        await page.goBack();
        await page.locator('#loginForm').waitFor();
        assert.equal(await page.locator('#shellNavigation').count(), 0);
        console.log('Playwright: sign-in, navigation, responsive dialogs, import, logs, theme, accounts, 2FA and audit passed.');
    } finally {
        if (browser) await browser.close();
        if (processHandle && processHandle.exitCode === null) {
            const exited = new Promise(resolve => processHandle.once('exit', resolve));
            processHandle.kill();
            await exited;
        }
        const target = path.resolve(state);
        if (path.dirname(target) === path.resolve(os.tmpdir()) && path.basename(target).startsWith('proxypermit-playwright-')) {
            rmSync(target, { recursive: true, force: true });
        }
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
