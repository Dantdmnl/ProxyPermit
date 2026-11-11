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
    let pending = false;

    async function request(action, data = {}) {
        const response = await fetch('/api/proxy/ha-replication/' + action, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': panel.dataset.csrf },
            body: JSON.stringify(data),
            signal: AbortSignal.timeout(15000)
        });
        if (!response.ok) throw new Error();
        return response.json();
    }

    async function refresh() {
        try {
            const result = await request('status');
            const current = result.status;
            const synchronized = current?.state === 'synchronized';
            status.textContent = I18n.t(!result.paired ? 'Nodes zijn nog niet gekoppeld.'
                : synchronized ? 'Beleid gesynchroniseerd.' : 'Synchronisatie wacht op de andere node.');
            status.dataset.state = !result.paired ? '' : synchronized ? 'synchronized' : 'pending';
            for (const [name, value] of [['published', current?.primaryRevision ?? result.revision], ['applied', current?.revision]]) {
                document.getElementById('ha-policy-' + name + '-row').hidden = !value;
                document.getElementById('ha-policy-' + name).textContent = value?.slice(0, 12) || '';
            }
            const checked = document.getElementById('ha-policy-checked');
            const checkedAt = panel.dataset.role === 'b' ? current?.primaryChecked : current?.checked;
            checked.closest('#ha-policy-checked-row').hidden = !checkedAt;
            if (checkedAt) {
                const time = new Date(checkedAt * 1000);
                checked.dateTime = time.toISOString();
                checked.textContent = new Intl.DateTimeFormat(I18n.tag, { dateStyle: 'short', timeStyle: 'medium' }).format(time);
            }
            disconnect.hidden = !result.paired;
            const join = document.getElementById('ha-policy-join');
            if (join) join.hidden = result.paired;
            const invite = panel.querySelector('[data-ha-policy="invite"]');
            if (invite) invite.hidden = result.paired;
            if (output && result.paired) {
                code.value = '';
                output.hidden = true;
            }
        } catch {
            status.dataset.state = '';
            status.textContent = I18n.t('Synchronisatiestatus kon niet worden geladen.');
        }
    }
    async function refreshVip() {
        const target = document.getElementById('ha-policy-vip');
        try {
            const response = await fetch('/api/status', { signal: AbortSignal.timeout(5000) });
            if (!response.ok) return;
            const node = await response.json();
            target.textContent = I18n.t(node.vip_active ? 'Virtueel IP actief op deze node.' : 'Virtueel IP niet actief op deze node.');
        } catch {
            target.textContent = '';
        }
    }

    async function perform(action) {
        if (pending) return;
        if (action === 'join' && !window.confirm(I18n.t('Node B gaat het actieve gedeelde beleid van A gebruiken. Doorgaan?'))) return;
        pending = true;
        buttons.forEach(control => { control.disabled = true; });
        feedback.textContent = '';
        try {
            const result = await request(action, { code: code.value.trim() });
            code.value = result.code || '';
            if (output) output.hidden = !result.code;
            if (result.code) code.select();
            await refresh();
        } catch {
            feedback.textContent = I18n.t('HA replication request failed. Check the peer and TLS configuration.');
        } finally {
            pending = false;
            buttons.forEach(control => { control.disabled = false; });
        }
    }
    for (const button of buttons) button.addEventListener('click', () => {
        if (button === disconnect) {
            disconnectDialog.returnValue = '';
            disconnectDialog.showModal();
        } else {
            perform(button.dataset.haPolicy);
        }
    });
    disconnectDialog.addEventListener('close', () => {
        if (disconnectDialog.returnValue === 'disconnect') perform('disconnect');
    });
    refresh();
    refreshVip();
    window.setInterval(() => { if (!pending) { refresh(); refreshVip(); } }, 10000);
})();
