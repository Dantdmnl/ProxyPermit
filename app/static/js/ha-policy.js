'use strict';

(() => {
    const panel = document.getElementById('ha-policy');
    if (!panel) return;
    const code = document.getElementById('ha-policy-code');
    const output = document.getElementById('ha-policy-result');
    const status = document.getElementById('ha-policy-status');
    const feedback = document.getElementById('ha-policy-feedback');
    const buttons = panel.closest('.settings-panel').querySelectorAll('[data-ha-policy]');
    const disconnect = document.querySelector('[data-ha-policy="disconnect"]');
    const disconnectDialog = document.getElementById('ha-disconnect-dialog');
    const disconnectForm = document.getElementById('ha-disconnect-form');
    const rotate = panel.querySelector('[data-ha-policy="rotate-key"]');
    const keyDialog = document.getElementById('ha-key-dialog');
    const keyForm = document.getElementById('ha-key-form');
    let pending = false;
    let statusSequence = 0;
    let protectionExpiry;
    const protection = document.getElementById('ha-protection-status');
    const protectionLabels = {
        'not-configured': 'Quorum and fencing are not configured',
        protected: 'Quorum confirmed; fencing configured',
        degraded: 'Cluster protection needs attention',
        stopped: 'Cluster resource is stopped',
        unsafe: 'Conflicting cluster ownership detected',
        unknown: 'Cluster protection could not be confirmed'
    };

    function showProtection(evidence) {
        clearTimeout(protectionExpiry);
        let state = Object.hasOwn(protectionLabels, evidence?.state) ? evidence.state : 'unknown';
        const lifetime = Number.isInteger(evidence?.validFor) ? Math.min(20, evidence.validFor) : 0;
        if (state !== 'unknown' && state !== 'not-configured' && lifetime <= 0) {
            state = 'unknown';
        }
        const reasons = [];
        const credentialExpired = evidence?.localFencingCredential === 'expired';
        if (state === 'degraded' || state === 'stopped') {
            if (evidence.quorate === false) reasons.push('No cluster quorum');
            if (evidence.witnessConnected === false) reasons.push('Witness connection not confirmed');
            if (evidence.fencingConfigured === false) reasons.push('Fencing configuration incomplete');
            else if (evidence.fencingReady === false && !credentialExpired) reasons.push('Fencing readiness not confirmed');
            if (evidence.policySafe === false) reasons.push('Cluster policy could not be verified');
            if (evidence.owners === 0) reasons.push('No node currently holds the virtual IP');
            if (state === 'degraded' && !reasons.length) reasons.push('Virtual IP ownership or resource health could not be verified');
        }
        if (state !== 'unknown' && state !== 'not-configured' && credentialExpired) {
            reasons.push('Local Proxmox fencing credential expired');
        }
        protection.textContent = [protectionLabels[state], ...reasons].map(label => I18n.t(label)).join(' · ');
        const checked = document.getElementById('ha-protection-checked');
        const fresh = state !== 'unknown' && state !== 'not-configured' && Number.isInteger(evidence?.checked);
        checked.closest('#ha-protection-checked-row').hidden = !fresh;
        if (fresh) showTime(checked, evidence.checked);
        if (state !== 'unknown' && state !== 'not-configured') {
            protectionExpiry = setTimeout(showProtection, lifetime * 1000);
        }
    }

    function showTime(element, epoch) {
        const time = new Date(epoch * 1000);
        element.dateTime = time.toISOString();
        element.textContent = new Intl.DateTimeFormat(I18n.tag, { dateStyle: 'short', timeStyle: 'medium' }).format(time);
    }

    async function request(action, data = {}) {
        const response = await fetch('/api/proxy/ha-replication/' + action, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': panel.dataset.csrf },
            body: JSON.stringify(data),
            signal: AbortSignal.timeout(15000)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(I18n.t(result.error || 'HA replication request failed. Check the peer and TLS configuration.'));
        return result;
    }

    async function refresh() {
        const sequence = ++statusSequence;
        try {
            const result = await request('status');
            if (sequence !== statusSequence) return;
            const current = result.status;
            showProtection(result.protection);
            const synchronized = current?.state === 'synchronized';
            status.textContent = I18n.t(!result.paired ? 'Nodes zijn nog niet gekoppeld.'
                : current?.reason === 'stale-primary' ? 'Policy history does not match. Review recovery on A before synchronizing.'
                : synchronized ? 'Beleid gesynchroniseerd.' : 'Synchronisatie wacht op de andere node.');
            status.dataset.state = !result.paired ? '' : synchronized ? 'synchronized' : 'pending';
            for (const [name, value] of [['published', current?.primaryRevision ?? result.revision], ['applied', current?.revision]]) {
                document.getElementById('ha-policy-' + name + '-row').hidden = !value;
                document.getElementById('ha-policy-' + name).textContent = value?.slice(0, 12) || '';
            }
            const checked = document.getElementById('ha-policy-checked');
            const checkedAt = panel.dataset.role === 'b' ? current?.primaryChecked : current?.checked;
            checked.closest('#ha-policy-checked-row').hidden = !checkedAt;
            if (checkedAt) showTime(checked, checkedAt);
            if (disconnect) disconnect.hidden = !result.paired;
            const credential = result.credential?.state || 'idle';
            document.getElementById('ha-key-state-row').hidden = !result.paired;
            const keyLabels = { idle: 'Peer key active', prepared: 'Waiting for B to accept the new key',
                committed: 'New key active; waiting for confirmation from B', complete: 'Peer key renewed',
                expired: 'Key transition expired. Re-pair the nodes.', invalid: 'Peer credential state needs operator review.' };
            document.getElementById('ha-key-state').textContent = I18n.t(keyLabels[credential] || keyLabels.invalid);
            if (rotate) {
                rotate.hidden = !result.paired;
                rotate.disabled = pending || !['idle', 'complete'].includes(credential);
            }
            const join = document.getElementById('ha-policy-join');
            if (join) join.hidden = result.paired;
            const invite = panel.querySelector('[data-ha-policy="invite"]');
            if (invite) invite.hidden = result.paired;
            if (output && result.paired) {
                code.value = '';
                output.hidden = true;
            }
        } catch {
            if (sequence !== statusSequence) return;
            status.dataset.state = '';
            status.textContent = I18n.t('Synchronisatiestatus kon niet worden geladen.');
            showProtection();
        }
    }
    let vipSequence = 0, vipExpiry;
    const vipStatus = document.getElementById('ha-policy-vip');
    const proxyStatus = document.getElementById('ha-policy-proxy');
    function clearNodeStatus() {
        vipStatus.textContent = I18n.t('VIP-status niet bevestigd');
        proxyStatus.textContent = I18n.t('Proxy status could not be confirmed');
    }
    async function refreshVip() {
        const sequence = ++vipSequence;
        try {
            const response = await fetch('/api/status', { signal: AbortSignal.timeout(5000) });
            if (!response.ok) throw new Error();
            const node = await response.json();
            if (sequence !== vipSequence) return;
            clearTimeout(vipExpiry);
            vipExpiry = setTimeout(clearNodeStatus, 12000);
            vipStatus.textContent = I18n.t(node.vip_active ? 'Virtueel IP actief op deze node.' : 'Virtueel IP niet actief op deze node.');
            proxyStatus.textContent = I18n.t(node.tinyproxy_active === false ? 'Proxy service is stopped'
                : node.tinyproxy_active !== true ? 'Proxy status could not be confirmed'
                : node.tinyproxy_check_status === 'unresponsive' ? 'Proxy service is running but not responding'
                : 'Proxy service is running');
        } catch {
            if (sequence !== vipSequence) return;
            clearNodeStatus();
        }
    }

    window.addEventListener('proxypermit:connection', event => {
        if (event.detail.state !== 'online') {
            vipSequence++;
            statusSequence++;
            clearTimeout(vipExpiry);
            clearNodeStatus();
            showProtection();
        } else { refreshVip(); refresh(); }
    });

    async function perform(action, fields = {}) {
        if (pending) return;
        if (action === 'join' && !window.confirm(I18n.t('Node B gaat het actieve gedeelde beleid van A gebruiken. Doorgaan?'))) return;
        pending = true;
        statusSequence++;
        buttons.forEach(control => { control.disabled = true; });
        feedback.textContent = '';
        try {
            const result = await request(action, { code: code.value.trim(), ...fields });
            code.value = result.code || '';
            if (output) output.hidden = !result.code;
            if (result.code) code.select();
            await refresh();
            return true;
        } catch (error) {
            feedback.textContent = error.message || I18n.t('HA replication request failed. Check the peer and TLS configuration.');
            return false;
        } finally {
            pending = false;
            buttons.forEach(control => { control.disabled = false; });
            await refresh();
        }
    }
    for (const button of buttons) button.addEventListener('click', () => {
        if (button === rotate) {
            keyForm.reset();
            document.getElementById('ha-key-error').textContent = '';
            keyDialog.showModal();
            keyForm.elements.password.focus();
        } else if (button === disconnect) {
            disconnectForm.reset();
            document.getElementById('ha-disconnect-error').textContent = '';
            disconnectDialog.showModal();
            disconnectForm.elements.password.focus();
        } else {
            perform(button.dataset.haPolicy);
        }
    });
    if (disconnectForm) {
        protectDialogDismiss(disconnectDialog, [document.getElementById('ha-disconnect-cancel')],
            () => [...disconnectForm.querySelectorAll('input')].some(input => input.value), () => pending);
        disconnectDialog.addEventListener('close', () => disconnectForm.reset());
        disconnectForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (pending || !disconnectForm.reportValidity()) return;
            const submit = disconnectForm.querySelector('[type="submit"]');
            submit.disabled = true;
            const success = await perform('disconnect', { confirmed: true,
                password: disconnectForm.elements.password.value, otp: disconnectForm.elements.otp?.value || '' });
            submit.disabled = false;
            if (success) disconnectDialog.close();
            else document.getElementById('ha-disconnect-error').textContent = feedback.textContent;
        });
    }
    if (keyForm) {
        protectDialogDismiss(keyDialog, [document.getElementById('ha-key-cancel')],
            () => [...keyForm.querySelectorAll('input')].some(input => input.value), () => pending);
        keyDialog.addEventListener('close', () => keyForm.reset());
        keyForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (pending || !keyForm.reportValidity()) return;
            const submit = keyForm.querySelector('[type="submit"]');
            submit.disabled = true;
            const success = await perform('rotate-key', { password: keyForm.elements.password.value,
                otp: keyForm.elements.otp?.value || '' });
            submit.disabled = false;
            if (success) keyDialog.close();
            else document.getElementById('ha-key-error').textContent = feedback.textContent;
        });
    }
    refresh();
    refreshVip();
    window.setInterval(() => { if (!pending) { refresh(); refreshVip(); } }, 10000);
})();
