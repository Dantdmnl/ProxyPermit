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

async function assertCredentialSpacing(dialog) {
    const password = await dialog.locator('input[type="password"]').boundingBox();
    const footer = await dialog.locator('.workspace-dialog-footer').boundingBox();
    const [cancel, confirm] = await Promise.all(
        (await dialog.locator('.workspace-dialog-footer button').all()).map(button => button.boundingBox())
    );
    assert.ok(footer.y - (password.y + password.height) >= 12, 'Keep breathing room below the password field');
    assert.ok(cancel.height >= 40 && confirm.height >= 40, 'Keep comfortable button targets');
    const gap = Math.abs(confirm.y - cancel.y) < 2
        ? confirm.x - (cancel.x + cancel.width)
        : confirm.y - (cancel.y + cancel.height);
    assert.ok(gap >= 11, 'Separate cancel and confirm when adjacent or wrapped');
    assert.ok(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth));
}

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
            manager: { password_hash: hash, revision: 'fixture-manager', role: 'manager' },
            monitor: { password_hash: hash, revision: 'fixture-monitor', role: 'monitor' }
        }));
        writeFileSync(file('filter.txt'), '# isolated browser fixture\n');
        writeFileSync(file('tinyproxy.conf'), 'Port 3128\nAllow 127.0.0.1\n');
        const seed = () => writeFileSync(file('ha-peer.json'), JSON.stringify({ key: 'a'.repeat(64),
            credentialProtocol: 1, credentialChecked: Math.floor(Date.now() / 1000) }));
        seed();
        writeFileSync(file('cluster-status.json'), JSON.stringify({ state: 'not-configured' }));
        writeFileSync(file('tls-status.json'), JSON.stringify({ state: 'valid', expires: '2027-02-14 12:00 UTC', fingerprint: 'c'.repeat(64) }));
        const fixture = [
            'from app.web import manager, proxy, settings, ha_replication',
            'proxy.activation_supported = lambda: True',
            'settings.activation_supported = lambda: True',
            'import json,os; from pathlib import Path; from app.core import cluster',
            'cluster.read_status = lambda **kwargs: json.loads(Path(os.environ["PP_TEST_CLUSTER_STATUS"]).read_text())',
            'cluster.reserved = lambda: bool(cluster.read_status().get("migrationPhase"))',
            'settings.cluster_setup.web_enabled = lambda: Path(os.environ["PP_TEST_CLUSTER_STATUS"]).with_name("cluster-web-enabled").exists()',
            'settings.cluster_setup.read_readiness = lambda **kwargs: dict(planRevision="")',
            'settings.management_tls_status = lambda: json.loads(Path(os.environ["PP_TEST_TLS_STATUS"]).read_text())',
            'from app.core import management_https as https_control',
            'from app.web import https_settings; import subprocess,types,time',
            'https_control.REQUEST = Path(os.environ["PP_TEST_TLS_STATUS"]).with_name("https-request.json")',
            'https_job = https_control.REQUEST.with_name("https-job.json")',
            'https_control.available = settings.https_control_available = lambda: True',
            'https_control.unit_busy = lambda: False',
            'https_control.snapshot = lambda: dict(revision="d"*64,names=["DNS:localhost","IP:127.0.0.1"],managed=True,canEnable=False,canRenew=True,node="fixture-a",role="a")',
            'https_control.result = lambda: json.loads(https_job.read_text()) if https_job.exists() else None',
            'def https_launch(args, **kwargs):\n assert args == https_control.COMMAND\n job=json.loads(https_control.REQUEST.read_text())\n assert "password" not in job and "otp" not in job\n https_job.write_text(json.dumps(dict(id=job["id"],action=job["action"],created=job["created"],phase="complete")))\n https_control.REQUEST.unlink()\n return subprocess.CompletedProcess(args,0)',
            'https_settings.subprocess = types.SimpleNamespace(run=https_launch,TimeoutExpired=subprocess.TimeoutExpired,SubprocessError=subprocess.SubprocessError)',
            'ha_replication.snapshot("Port 3128\\nAllow 127.0.0.1\\n", "# isolated browser fixture\\n")',
            `manager.app.run(host="127.0.0.1", port=${port}, use_reloader=False)`
        ].join('\n');
        child = spawn(python, ['-c', fixture], { cwd: root, stdio: 'ignore', env: {
            ...process.env, FLASK_SECRET_KEY: 'isolated-ha-browser-fixture', ENABLE_AUTH: 'true', ENABLE_RADIUS: 'false',
            AUTH_ACCOUNTS_FILE: file('accounts.json'), SESSION_COOKIE_SECURE: 'false', MANAGER_SCHEME: 'http',
            DEPLOYMENT_MODE: 'ha', PROXYPERMIT_MANAGED_HA: 'true', PROXYPERMIT_HA_NODE: 'a', DEMO_MODE: 'true',
            PROXYPERMIT_HA_INTERFACE: 'ens18',
            PROXYPERMIT_PUBLIC_NODE_LABEL: 'Lab West',
            NODE0_ADDRESS: '192.0.2.11', NODE1_ADDRESS: '192.0.2.12', VIP_ADDRESS: '192.0.2.10',
            WHITELIST_PATH: file('filter.txt'), TINYPROXY_CONFIG_PATH: file('tinyproxy.conf'),
            SESSION_STATE_FILE: file('session.json'), AUDIT_LOG_PATH: file('audit.log'),
            PP_TEST_CLUSTER_STATUS: file('cluster-status.json'),
            PP_TEST_TLS_STATUS: file('tls-status.json'),
            PROXYPERMIT_ACCESS_SAFETY_FILE: file('access-safety.json'), WHITELIST_ENTRY_LOG_PATH: file('entries.json')
        } });
        for (let i = 0; ; i++) {
            try { if ((await fetch(base)).ok) break; } catch { /* Wait for fixture. */ }
            if (i > 80 || child.exitCode !== null) throw new Error('HA browser fixture did not start');
            await pause(100);
        }
        browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {});
        const page = await browser.newPage();
        await page.goto(base + '/?lang=en');
        assert.equal(await page.locator('#signin-node').textContent(), 'Node A · Lab West');
        await page.locator('#loginUsername').fill('admin');
        await page.locator('#loginPassword').fill(password);
        await page.locator('.signin-languages [lang="nl"]').click();
        await page.waitForFunction(() => document.documentElement.lang === 'nl');
        assert.equal(await page.locator('#loginPassword').inputValue(), password);
        await page.route('**/api/auth/login', async route => {
            assert.equal(route.request().headers()['x-proxypermit-login-node'], 'a');
            await route.fulfill({ status: 409, json: { nodeChanged: true } });
        }, { times: 1 });
        await page.locator('#loginButton').click();
        await page.waitForURL(url => url.searchParams.get('lang') === 'nl' && url.searchParams.get('node_changed') === '1');
        await page.locator('#loginFeedback').filter({ hasText: 'De beheernode is gewisseld' }).waitFor();
        assert.equal(await page.locator('#loginPassword').inputValue(), '');
        let readinessLifetime = 90;
        await page.route('**/api/settings/cluster-readiness', route => route.fulfill({ json: {
            state: 'preparation', validFor: readinessLifetime,
            checks: { managedHa: 'pass', time: 'blocked', packages: 'blocked', plan: 'pending',
                agent: 'pending', authentication: 'pending', powerTest: 'pending' }
        } }));
        assert.equal((await page.request.post(base + '/api/auth/login', { data: { username: 'admin', password } })).status(), 200);
        for (const language of ['en', 'nl']) {
            for (const theme of ['light', 'dark']) {
                for (const width of [360, 1440]) {
                    seed();
                    await page.setViewportSize({ width, height: 740 });
                    await page.goto(base + '/settings?lang=' + language);
                    assert.equal(await page.locator('#ha-management-node').textContent(), 'Node A · Lab West');
                    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
                    await page.locator('#cluster-preparation > summary').click();
                    await page.locator('[data-cluster-check="time"]').filter({ hasText: language === 'en' ? 'Action required' : 'Actie nodig' }).waitFor();
                    assert.equal(await page.locator('[data-cluster-check="powerTest"]').textContent(), language === 'en' ? 'Still to verify' : 'Nog te controleren');
                    if (width === 360 && theme === 'dark') {
                        const guide = page.locator('.cluster-plan-guide');
                        await guide.locator('summary').click();
                        for (const port of ['5405', '5403', '8006']) {
                            await guide.getByText(port, { exact: false }).waitFor();
                        }
                        const form = page.locator('#cluster-plan-form');
                        for (const [name, value] of Object.entries({ nodeAName: 'node-a', nodeBName: 'node-b',
                            nodeATarget: '8101', nodeBTarget: '8102', witness: '192.0.2.13', fenceAddress: '192.0.2.14' })) {
                            await form.locator(`[name="${name}"]`).fill(value);
                        }
                        await form.locator('button[type=submit]').click();
                        const link = page.locator('#cluster-plan-download');
                        await link.waitFor();
                        const fingerprint = await page.locator('#cluster-plan-fingerprint').textContent();
                        assert.match(fingerprint, /^[a-f0-9]{64}$/);
                        const downloadEvent = page.waitForEvent('download');
                        await link.click();
                        const planFile = await downloadEvent;
                        assert.equal(planFile.suggestedFilename(), 'cluster-plan.json');
                        const plan = JSON.parse(readFileSync(await planFile.path(), 'utf8'));
                        assert.deepEqual(plan.nodes.map(node => node.address), ['192.0.2.11', '192.0.2.12']);
                        await form.locator('[name=witness]').fill('192.0.2.10');
                        await link.waitFor({ state: 'hidden' });
                        await form.locator('button[type=submit]').click();
                        await page.locator('#cluster-plan-feedback[data-error]').waitFor();
                        await page.locator('#cluster-plan-file').setInputFiles(await planFile.path());
                        await link.waitFor();
                        assert.equal(await page.locator('#cluster-plan-fingerprint').textContent(), fingerprint);
                        assert.equal(await form.locator('[name=witness]').inputValue(), '192.0.2.13');
                        await page.locator('#cluster-plan-file').setInputFiles({ name: 'foreign.json',
                            mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({
                                ...plan, nodes: [{ ...plan.nodes[0], address: '192.0.2.99' }, plan.nodes[1]]
                            })) });
                        await page.locator('#cluster-plan-feedback[data-error]').waitFor();
                        await link.waitFor({ state: 'hidden' });
                    }
                    await page.locator('#management-tls-title').filter({ hasText: language === 'en' ? 'Management HTTPS' : 'HTTPS voor beheer' }).waitFor();
                    assert.equal(await page.locator('.management-tls-fingerprint').first().textContent(), 'c'.repeat(64));
                    const button = page.locator('[data-ha-policy="rotate-key"]');
                    await button.waitFor();
                    assert.equal(await page.locator('#ha-protection-status').textContent(), language === 'en'
                        ? 'Quorum and fencing are not configured' : 'Quorum en fencing zijn niet ingesteld');
                    await button.click();
                    const dialog = page.locator('#ha-key-dialog');
                    const input = dialog.locator('[name="password"]');
                    await input.waitFor();
                    await assertCredentialSpacing(dialog);
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
                    const revoke = page.locator('[data-ha-policy="disconnect"]');
                    const maintenance = revoke.locator('xpath=..');
                    if (!await maintenance.evaluate(el => el.open)) await maintenance.locator(':scope > summary').click();
                    await revoke.click();
                    const revocation = page.locator('#ha-disconnect-dialog');
                    const current = revocation.locator('[name="password"]');
                    await current.waitFor();
                    await assertCredentialSpacing(revocation);
                    assert.equal(await current.evaluate(el => document.activeElement === el), true);
                    const bounds = await revocation.boundingBox();
                    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
                    await current.fill('unsaved');
                    page.once('dialog', event => event.dismiss());
                    await page.keyboard.press('Escape');
                    assert.equal(await current.inputValue(), 'unsaved');
                    assert.equal(await revocation.isVisible(), true);
                    page.once('dialog', event => event.accept());
                    await page.keyboard.press('Escape');
                    await revocation.waitFor({ state: 'hidden' });
                    await page.waitForFunction(() => document.querySelector('#ha-disconnect-form [name="password"]').value === '');
                    await page.locator('#https-renew').waitFor({ state: 'visible' });
                    await page.locator('#https-renew').click();
                    const httpsDialog = page.locator('#https-dialog');
                    const httpsPassword = httpsDialog.locator('[name="password"]');
                    await httpsPassword.waitFor();
                    await assertCredentialSpacing(httpsDialog);
                    assert.equal(await httpsPassword.evaluate(el => document.activeElement === el), true);
                    await httpsPassword.fill('draft');
                    page.once('dialog', event => event.dismiss());
                    await page.keyboard.press('Escape');
                    assert.equal(await httpsPassword.inputValue(), 'draft');
                    page.once('dialog', event => event.accept());
                    await page.keyboard.press('Escape');
                    await httpsDialog.waitFor({ state: 'hidden' });
                    await page.waitForFunction(() => document.querySelector('#https-form [name="password"]').value === '');
                }
            }
        }
        await page.setViewportSize({ width: 1440, height: 900 });
        readinessLifetime = 1;
        await page.goto(base + '/settings?lang=en');
        await page.locator('#cluster-preparation > summary').click();
        await page.locator('#cluster-readiness-message').filter({ hasText: 'OS prerequisite checks are unavailable or expired.' }).waitFor();
        assert.equal(await page.locator('[data-cluster-check="time"]').textContent(), 'Not confirmed');
        readinessLifetime = 90;
        seed();
        await page.goto(base + '/settings?lang=nl');
        await page.locator('#https-renew').click();
        const httpsForm = page.locator('#https-form');
        await httpsForm.locator('[name="password"]').fill('incorrect');
        await httpsForm.locator('[type="submit"]').click();
        await page.locator('#https-error').filter({ hasText: 'Wachtwoord of authenticatorcode klopt niet.' }).waitFor();
        await httpsForm.locator('[name="password"]').fill(password);
        await httpsForm.locator('[type="submit"]').click();
        await page.locator('#https-dialog').waitFor({ state: 'hidden' });
        await page.locator('#https-feedback').filter({ hasText: 'Het HTTPS-onderhoud is afgerond.' }).waitFor();
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
        const revoke = page.locator('[data-ha-policy="disconnect"]');
        await revoke.locator('xpath=..').locator(':scope > summary').click();
        await revoke.click();
        const revokeForm = page.locator('#ha-disconnect-form');
        await revokeForm.locator('[name="password"]').fill('incorrect');
        await revokeForm.locator('[type="submit"]').click();
        await page.locator('#ha-disconnect-error').filter({ hasText: 'Wachtwoord of authenticatorcode klopt niet.' }).waitFor();
        assert.equal(JSON.parse(readFileSync(file('ha-peer.json'))).key, peer.key);
        await revokeForm.locator('[name="password"]').fill(password);
        await revokeForm.locator('[type="submit"]').click();
        await page.locator('#ha-disconnect-dialog').waitFor({ state: 'hidden' });
        assert.equal(require('node:fs').existsSync(file('ha-peer.json')), false);
        const audit = readFileSync(file('audit.log'), 'utf8');
        assert.ok(audit.includes('ha_replication_disconnected'));
        assert.ok(audit.includes('admin'));
        assert.ok(!audit.includes(password) && !audit.includes(peer.key) && !audit.includes(peer.rotation.key));
        seed();
        writeFileSync(file('cluster-status.json'), JSON.stringify({ state: 'protected', validFor: 20 }));
        await page.reload();
        await page.locator('#ha-protection-status').filter({ hasText: 'Quorum bevestigd; fencing ingesteld' }).waitFor();
        await page.context().setOffline(true);
        await page.locator('#ha-protection-status').filter({ hasText: 'De clusterbescherming kon niet worden bevestigd' }).waitFor();
        await page.context().setOffline(false);
        writeFileSync(file('cluster-status.json'), JSON.stringify({
            state: 'degraded', validFor: 20, quorate: true, witnessConnected: false,
            fencingConfigured: true, fencingReady: true, policySafe: true
        }));
        for (const [language, reason] of [
            ['en', 'Witness connection not confirmed'],
            ['nl', 'Verbinding met de witness niet bevestigd']
        ]) {
            await page.goto(base + '/settings?lang=' + language);
            const protection = page.locator('#ha-protection-status');
            await protection.filter({ hasText: reason }).waitFor();
            assert.ok(!(await protection.textContent()).includes(language === 'en' ? 'No cluster quorum' : 'Geen quorum in het cluster'));
        }
        writeFileSync(file('cluster-status.json'), JSON.stringify({ state: 'unsafe', validFor: 20 }));
        await page.reload();
        await page.locator('#ha-protection-status').filter({ hasText: 'Tegenstrijdig clusterbezit gedetecteerd' }).waitFor();
        const monitor = await browser.newContext();
        assert.equal((await monitor.request.post(base + '/api/auth/login', { data: { username: 'monitor', password } })).status(), 200);
        const readOnly = await monitor.newPage();
        await readOnly.goto(base + '/settings?lang=en');
        assert.equal(await readOnly.locator('[data-ha-policy="rotate-key"]').count(), 0);
        assert.equal((await monitor.request.post(base + '/api/proxy/ha-replication/rotate-key', { data: { password } })).status(), 403);
        assert.equal(await readOnly.locator('#https-control').count(), 0);
        assert.equal(await readOnly.locator('#cluster-preparation').count(), 0);
        assert.equal((await monitor.request.get(base + '/api/settings/cluster-readiness')).status(), 403);
        assert.equal((await monitor.request.get(base + '/api/settings/https')).status(), 403);
        await monitor.close();
        const proxyManager = await browser.newContext();
        assert.equal((await proxyManager.request.post(base + '/api/auth/login', { data: { username: 'manager', password } })).status(), 200);
        const managerPage = await proxyManager.newPage();
        await managerPage.goto(base + '/settings?lang=en');
        assert.equal(await managerPage.locator('#https-control').count(), 0);
        assert.equal(await managerPage.locator('#cluster-preparation').count(), 0);
        assert.equal((await proxyManager.request.get(base + '/api/settings/https/ca')).status(), 403);
        assert.equal((await proxyManager.request.post(base + '/api/settings/https/jobs', { data: { action: 'renew', password } })).status(), 403);
        await proxyManager.close();
        // A stale console keeps its open form in place and sends no further writes.
        await page.goto(base + '/settings?lang=nl');
        await page.locator('.ha-maintenance > summary').click();
        await page.locator('[data-ha-policy="disconnect"]').click();
        const pendingPassword = page.locator('#ha-disconnect-form [name="password"]');
        await pendingPassword.fill('local unsaved fixture');
        await page.route('**/connection', route => route.fulfill({
            status: 204, headers: { 'X-ProxyPermit-Reachable': '1', 'X-ProxyPermit-Node': 'b' }
        }));
        await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
        const notice = page.locator('#connectivityNotice');
        await notice.locator('strong').filter({ hasText: 'De beheernode is gewisseld' }).waitFor();
        assert.equal(await pendingPassword.inputValue(), 'local unsaved fixture');
        let requestsAfterSwitch = 0;
        await page.route('**/api/auth/logout', route => { requestsAfterSwitch++; return route.abort(); });
        assert.equal(await page.evaluate(async () => {
            try { await fetch('/api/auth/logout', { method: 'POST' }); return false; }
            catch { return true; }
        }), true);
        assert.equal(requestsAfterSwitch, 0);
        await page.unroute('**/connection');
        await page.unroute('**/api/auth/logout');
        // A lost activation response must not strand the browser on HTTP or replay the POST.
        let activationPosts = 0;
        await page.route('**/api/settings/https', route => route.fulfill({ json: {
            revision: 'd'.repeat(64), names: ['IP:127.0.0.1'], node: 'fixture-a', role: 'a',
            managed: false, canEnable: true, canRenew: false,
            job: { phase: 'prepared', plan: 'a'.repeat(32), expires: Math.floor(Date.now()/1000) + 600,
                certificate: { caFingerprint: 'c'.repeat(64) } }
        } }));
        await page.route('**/api/settings/https/jobs', route => { activationPosts++; return route.abort(); });
        await page.goto(base + '/settings?lang=en');
        await page.locator('#https-trusted').check();
        await page.locator('#https-activate').click();
        await page.locator('#https-form [name="password"]').fill(password);
        await page.locator('#https-form [type="submit"]').click();
        await page.locator('#https-dialog').waitFor({ state: 'hidden' });
        await page.locator('#https-feedback').filter({ hasText: 'The activation reply was lost.' }).waitFor();
        const secure = new URL(base); secure.protocol = 'https:'; secure.pathname = '/settings';
        assert.equal(await page.locator('#https-feedback a').getAttribute('href'), secure.href);
        assert.equal(await page.locator('[data-https-transition]').getAttribute('href'), secure.href);
        assert.equal(activationPosts, 1);
        await page.unroute('**/api/settings/cluster-readiness');
        writeFileSync(file('cluster-web-enabled'), '');
        let planRevision = '', job, stagedPlan, packagesPrepared = false;
        await page.route('**/api/settings/cluster-readiness', route => route.fulfill({ json: {
            state: 'preparation', validFor: 90, webAvailable: true, planRevision, packagesPrepared,
            planLocked: packagesPrepared, checks: { time: 'pass' }, job, stagedPlan
        } }));
        await page.route('**/api/settings/cluster-plan/jobs', async route => {
            const data = route.request().postDataJSON();
            assert.equal(data.password, password);
            assert.equal(data.confirmed, true);
            assert.match(route.request().headers()['x-csrf-token'], /.+/);
            const id = data.action === 'stage' ? 'a'.repeat(32) : 'b'.repeat(32);
            if (data.action === 'stage') {
                assert.equal(data.plan.nodes[0].name, os.hostname().split('.')[0]);
                planRevision = data.planHash;
                stagedPlan = data.plan;
            } else {
                assert.equal(data.action, 'prepare');
                assert.equal(data.planHash, planRevision);
                assert.equal(data.plan, undefined);
                packagesPrepared = true;
            }
            job = { id, action: data.action, phase: 'complete' };
            await route.fulfill({ status: 202, json: { id } });
        });
        await page.setViewportSize({ width: 360, height: 740 });
        await page.goto(base + '/settings?lang=nl');
        await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
        await page.locator('#cluster-preparation > summary').click();
        const clusterForm = page.locator('#cluster-plan-form');
        assert.equal(await clusterForm.locator('[name=nodeAName]').inputValue(), os.hostname().split('.')[0]);
        for (const [name, value] of Object.entries({ nodeBName: 'node-b', nodeATarget: '8101',
            nodeBTarget: '8102', witness: '192.0.2.13', fenceAddress: '192.0.2.14' })) {
            await clusterForm.locator(`[name="${name}"]`).fill(value);
        }
        await clusterForm.locator('[type=submit]').click();
        const firewall = page.locator('#cluster-plan-firewall');
        await firewall.locator('summary').click();
        await firewall.locator('code').filter({ hasText: /port 5405 proto udp/ }).waitFor();
        assert.ok(await firewall.evaluate(element => element.scrollWidth <= element.clientWidth));
        const clusterDialog = page.locator('#cluster-plan-dialog');
        for (const [button, result] of [['#cluster-plan-stage', 'Het plan is op deze node opgeslagen.'],
            ['#cluster-plan-prepare', 'De clusterpakketten zijn voorbereid op deze node.']]) {
            await page.locator(button).click();
            assert.equal(await clusterDialog.locator('[name=password]').evaluate(el => document.activeElement === el), true);
            await assertCredentialSpacing(clusterDialog);
            await clusterDialog.locator('[name=password]').fill(password);
            await clusterDialog.locator('[type=submit]').click();
            await page.locator('#cluster-plan-job-status').filter({ hasText: result }).waitFor();
        }
        assert.equal(await page.locator('#cluster-local-saved code').textContent(), planRevision);
        assert.equal(await page.locator('#cluster-plan-prepare').isDisabled(), true);
        await clusterForm.locator('[name=witness]').fill('192.0.2.55');
        await page.locator('#cluster-plan-load').click();
        assert.equal(await clusterForm.locator('[name=witness]').inputValue(), stagedPlan.witness);
        await page.unroute('**/api/settings/cluster-readiness');
        writeFileSync(file('cluster-status.json'), JSON.stringify({
            state: 'unknown', migrationPhase: 'reserving', validFor: 20
        }));
        for (const [language, theme, width] of [['en', 'light', 1440], ['nl', 'dark', 360]]) {
            await page.setViewportSize({ width, height: 740 });
            await page.goto(base + '/settings?lang=' + language);
            await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
            const migration = page.locator('#cluster-migration');
            await migration.locator('summary').focus();
            await page.keyboard.press('Enter');
            await migration.getByText(language === 'en' ? 'Reservation interrupted or in progress.' :
                'De reservering loopt nog of is onderbroken.', { exact: false }).waitFor();
            await migration.locator('code').filter({ hasText: '--phase status' }).waitFor();
            assert.ok(await migration.evaluate(element => element.scrollWidth <= element.clientWidth));
        }
        await page.route('**/api/settings/cluster-readiness', route => route.fulfill({ json: {
            state: 'migration', phase: 'activating', validFor: 1
        } }));
        await page.goto(base + '/settings?lang=en');
        await page.locator('#cluster-migration > summary').click();
        const phase = page.locator('#cluster-migration-phase');
        await phase.filter({ hasText: 'Activation requested.' }).waitFor();
        await phase.filter({ hasText: 'The local migration phase could not be confirmed.' }).waitFor();
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
