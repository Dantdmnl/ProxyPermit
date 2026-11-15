// Isolated HA UI fixture: no system services, network changes or real peer keys.
'use strict';
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const { mkdtempSync, readFileSync, writeFileSync, rmSync } = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const python = process.env.PP_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const password = 'isolated HA browser passphrase';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
    const state = mkdtempSync(path.join(os.tmpdir(), 'proxypermit-ha-browser-'));
    const file = name => path.join(state, name);
    let child, browser;
    try {
        const listener = net.createServer();
        await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
        const port = listener.address().port;
        await new Promise(resolve => listener.close(resolve));
        const base = `http://127.0.0.1:${port}`;
        const hash = execFileSync(python, ['-c', 'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))'],
            { input: password, encoding: 'utf8' }).trim();
        writeFileSync(file('accounts.json'), JSON.stringify({
            admin: { password_hash: hash, revision: 'fixture-admin', role: 'admin' },
            monitor: { password_hash: hash, revision: 'fixture-monitor', role: 'monitor' }
        }));
        writeFileSync(file('filter.txt'), '# isolated browser fixture\n');
        writeFileSync(file('tinyproxy.conf'), 'Port 3128\nAllow 127.0.0.1\n');
        const seed = () => writeFileSync(file('ha-peer.json'), JSON.stringify({ key: 'a'.repeat(64),
            credentialProtocol: 1, credentialChecked: Math.floor(Date.now() / 1000) }));
        seed();
        writeFileSync(file('cluster-status.json'), JSON.stringify({ state: 'not-configured' }));
        const fixture = [
            'from app.web import manager, proxy, settings, ha_replication',
            'proxy.activation_supported = lambda: True',
            'settings.activation_supported = lambda: True',
            'import json,os; from pathlib import Path; from app.core import cluster',
            'cluster.read_status = lambda: json.loads(Path(os.environ["PP_TEST_CLUSTER_STATUS"]).read_text())',
            'ha_replication.snapshot("Port 3128\\nAllow 127.0.0.1\\n", "# isolated browser fixture\\n")',
            `manager.app.run(host="127.0.0.1", port=${port}, use_reloader=False)`
        ].join('\n');
        child = spawn(python, ['-c', fixture], { cwd: root, stdio: 'ignore', env: {
            ...process.env, FLASK_SECRET_KEY: 'isolated-ha-browser-fixture', ENABLE_AUTH: 'true', ENABLE_RADIUS: 'false',
            AUTH_ACCOUNTS_FILE: file('accounts.json'), SESSION_COOKIE_SECURE: 'false', MANAGER_SCHEME: 'http',
            DEPLOYMENT_MODE: 'ha', PROXYPERMIT_MANAGED_HA: 'true', PROXYPERMIT_HA_NODE: 'a', DEMO_MODE: 'true',
            NODE0_ADDRESS: '192.0.2.11', NODE1_ADDRESS: '192.0.2.12', VIP_ADDRESS: '192.0.2.10',
            WHITELIST_PATH: file('filter.txt'), TINYPROXY_CONFIG_PATH: file('tinyproxy.conf'),
            SESSION_STATE_FILE: file('session.json'), AUDIT_LOG_PATH: file('audit.log'),
            PP_TEST_CLUSTER_STATUS: file('cluster-status.json'),
            PROXYPERMIT_ACCESS_SAFETY_FILE: file('access-safety.json'), WHITELIST_ENTRY_LOG_PATH: file('entries.json')
        } });
        for (let i = 0; ; i++) {
            try { if ((await fetch(base)).ok) break; } catch { /* Wait for fixture. */ }
            if (i > 80 || child.exitCode !== null) throw new Error('HA browser fixture did not start');
            await pause(100);
        }
        browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});
        const page = await browser.newPage();
        assert.equal((await page.request.post(base + '/api/auth/login', { data: { username: 'admin', password } })).status(), 200);
        for (const language of ['en', 'nl']) {
            for (const theme of ['light', 'dark']) {
                for (const width of [360, 1440]) {
                    seed();
                    await page.setViewportSize({ width, height: 740 });
                    await page.goto(base + '/settings?lang=' + language);
                    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
                    const button = page.locator('[data-ha-policy="rotate-key"]');
                    await button.waitFor();
                    assert.equal(await page.locator('#ha-protection-status').textContent(), language === 'en'
                        ? 'Quorum and fencing are not configured' : 'Quorum en fencing zijn niet ingesteld');
                    await button.click();
                    const dialog = page.locator('#ha-key-dialog');
                    const input = dialog.locator('[name="password"]');
                    await input.waitFor();
                    assert.equal(await input.evaluate(el => document.activeElement === el), true);
                    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                    const box = await dialog.boundingBox();
                    assert.ok(box.x >= 0 && box.x + box.width <= width + 1);
                    await input.fill('draft');
                    page.once('dialog', event => event.dismiss());
                    await page.keyboard.press('Escape');
                    assert.equal(await dialog.isVisible(), true);
                    assert.equal(await input.inputValue(), 'draft');
                    page.once('dialog', event => event.accept());
                    await page.keyboard.press('Escape');
                    await dialog.waitFor({ state: 'hidden' });
                    await page.waitForFunction(() => document.querySelector('#ha-key-form [name="password"]').value === '');
                }
            }
        }
        await page.setViewportSize({ width: 1440, height: 900 });
        seed();
        await page.goto(base + '/settings?lang=nl');
        await page.locator('[data-ha-policy="rotate-key"]').click();
        const form = page.locator('#ha-key-form');
        await form.locator('[name="password"]').fill('incorrect');
        await form.locator('[type="submit"]').click();
        await page.locator('#ha-key-error').filter({ hasText: 'Wachtwoord of authenticatorcode klopt niet.' }).waitFor();
        assert.equal(await page.locator('#ha-key-dialog').isVisible(), true);
        await form.locator('[name="password"]').fill(password);
        await form.locator('[type="submit"]').click();
        await page.locator('#ha-key-dialog').waitFor({ state: 'hidden' });
        await page.locator('#ha-key-state').filter({ hasText: 'Wachten tot B' }).waitFor();
        assert.equal(await page.locator('[data-ha-policy="rotate-key"]').isDisabled(), true);
        const peer = JSON.parse(readFileSync(file('ha-peer.json')));
        assert.equal(peer.rotation.state, 'prepared');
        assert.ok(!(await page.content()).includes(peer.rotation.key));
        assert.ok(!(await page.content()).includes(peer.key));
        writeFileSync(file('cluster-status.json'), JSON.stringify({ state: 'protected', validFor: 20 }));
        await page.reload();
        await page.locator('#ha-protection-status').filter({ hasText: 'Quorum bevestigd; fencing ingesteld' }).waitFor();
        await page.context().setOffline(true);
        await page.locator('#ha-protection-status').filter({ hasText: 'De clusterbescherming kon niet worden bevestigd' }).waitFor();
        await page.context().setOffline(false);
        writeFileSync(file('cluster-status.json'), JSON.stringify({ state: 'unsafe', validFor: 20 }));
        await page.reload();
        await page.locator('#ha-protection-status').filter({ hasText: 'Tegenstrijdig clusterbezit gedetecteerd' }).waitFor();
        const monitor = await browser.newContext();
        assert.equal((await monitor.request.post(base + '/api/auth/login', { data: { username: 'monitor', password } })).status(), 200);
        const readOnly = await monitor.newPage();
        await readOnly.goto(base + '/settings?lang=en');
        assert.equal(await readOnly.locator('[data-ha-policy="rotate-key"]').count(), 0);
        assert.equal((await monitor.request.post(base + '/api/proxy/ha-replication/rotate-key', { data: { password } })).status(), 403);
        await monitor.close();
        console.log('Playwright HA: NL/EN, light/dark, mobile/desktop, focus, discard confirmation, reauthentication and monitor restrictions passed.');
    } finally {
        if (browser) await browser.close();
        if (child && child.exitCode === null) {
            const stopped = new Promise(resolve => child.once('exit', resolve));
            child.kill();
            await stopped;
        }
        if (path.dirname(state) === path.resolve(os.tmpdir()) && path.basename(state).startsWith('proxypermit-ha-browser-')) {
            rmSync(state, { recursive: true, force: true });
        }
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
