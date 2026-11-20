'use strict';

(() => {
    const panel = document.getElementById('https-control');
    if (!panel) return;
    const form = document.getElementById('https-form');
    const dialog = document.getElementById('https-dialog');
    const feedback = document.getElementById('https-feedback');
    const error = document.getElementById('https-error');
    const buttons = ['prepare', 'renew', 'activate'].map(name => document.getElementById('https-' + name));
    const trusted = document.getElementById('https-trusted');
    const ca = document.getElementById('https-ca');
    let current, action, busy = false;
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const t = key => I18n.t(key);

    async function load() {
        const response = await fetch('/api/settings/https', { signal: AbortSignal.timeout(7000) });
        const state = await response.json();
        if (!response.ok) throw new Error(t(state.error || 'Cannot read HTTPS maintenance state. Review the node configuration.'));
        current = state;
        document.getElementById('https-node').textContent = t('Target node') + ': ' + state.node + ' · ' + state.role;
        const list = document.getElementById('https-names');
        list.replaceChildren(...state.names.map(name => { const item = document.createElement('li'); item.textContent = name; return item; }));
        buttons[0].hidden = !state.canEnable;
        buttons[1].hidden = !state.canRenew;
        if (!state.canEnable && !state.canRenew) feedback.textContent = t('This HTTPS certificate is managed outside ProxyPermit. Use its issuer\'s renewal process.');
        const prepared = state.job?.phase === 'prepared' && state.job.expires * 1000 > Date.now() && state.canEnable;
        document.getElementById('https-plan').hidden = !prepared;
        ca.hidden = !(state.managed || prepared);
        if (prepared) document.getElementById('https-ca-fingerprint').textContent = state.job.certificate.caFingerprint;
        buttons.forEach(button => { button.disabled = busy; });
        buttons[2].disabled = busy || !trusted.checked;
        return state;
    }

    function offerSecureAddress(message) {
            const url = new URL('/settings', location.href); url.protocol = 'https:';
            url.port = location.port || '80';
            window.dispatchEvent(new CustomEvent('proxypermit:https-transition', { detail: { url: url.href } }));
            const link = document.createElement('a'); link.href = url.href; link.textContent = t('Open HTTPS management');
            feedback.replaceChildren(document.createTextNode(t(message) + ' '), link);
    }

    async function watch(id, activating) {
        const deadline = Date.now() + 180000;
        if (activating && location.protocol === 'http:') {
            offerSecureAddress('HTTPS is being activated. Open the secure management address when ready.');
            // Navigation is deliberate: the new origin must pass the browser's
            // normal certificate checks, without cross-origin readiness probes.
            return;
        }
        while (Date.now() < deadline) {
            await wait(1500);
            try {
                const state = await load();
                if (state.job?.id !== id) continue;
                if (state.job.phase === 'failed') throw new Error(t(state.job.error));
                if (state.job.phase === 'prepared') { feedback.textContent = t('Certificate prepared. Download and verify the node CA before activating HTTPS.'); return; }
                if (state.job.phase === 'complete') {
                    feedback.textContent = t('HTTPS maintenance completed. Reload the page to refresh certificate details.');
                    return;
                }
            } catch (issue) {
                if (issue.message === t('HTTPS maintenance failed. Review the node settings and system log.')) throw issue;
                feedback.textContent = t('Waiting for the management interface to return. Do not submit the change again.');
            }
        }
        throw new Error(t('HTTPS maintenance did not finish. Review OS recovery before retrying.'));
    }

    buttons.forEach((button, index) => button.addEventListener('click', async () => {
        if (busy) return;
        try { await load(); } catch (issue) { feedback.textContent = issue.message; return; }
        action = ['prepare', 'renew', 'activate'][index];
        form.reset(); error.textContent = '';
        document.getElementById('https-dialog-title').textContent = t(index === 0 ? 'Prepare HTTPS' : index === 1 ? 'Renew certificate' : 'Activate HTTPS');
        dialog.showModal(); form.elements.password.focus();
    }));
    trusted.addEventListener('change', () => { buttons[2].disabled = busy || !trusted.checked; });
    protectDialogDismiss(dialog, [document.getElementById('https-cancel')],
        () => [...form.querySelectorAll('input')].some(input => input.value), () => busy);
    dialog.addEventListener('close', () => form.reset());
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !current || !form.reportValidity()) return;
        busy = true; error.textContent = ''; form.querySelector('[type="submit"]').disabled = true;
        buttons.forEach(button => { button.disabled = true; });
        try {
            const response = await fetch('/api/settings/https/jobs', {
                method: 'POST', signal: AbortSignal.timeout(10000),
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': panel.dataset.csrf },
                body: JSON.stringify({ action, revision: current.revision, plan: action === 'activate' ? current.job?.plan : '',
                    confirmed: true, trusted: trusted.checked, password: form.elements.password.value, otp: form.elements.otp?.value || '' })
            });
            const result = await response.json();
            if (!response.ok) { error.textContent = t(result.error); return; }
            dialog.close();
            feedback.textContent = t('HTTPS maintenance queued. Do not submit the change again.');
            await watch(result.id, action === 'activate');
        } catch (issue) {
            if (action === 'activate' && location.protocol === 'http:') {
                dialog.close();
                offerSecureAddress('The activation reply was lost. Check the secure management address before submitting again.');
            } else if (dialog.open) error.textContent = issue.message;
            else feedback.textContent = issue.message;
        } finally {
            busy = false; form.querySelector('[type="submit"]').disabled = false;
            await load().catch(() => {});
        }
    });
    load().catch(issue => { feedback.textContent = issue.message; });
})();
