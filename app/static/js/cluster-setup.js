'use strict';
(() => {
    const panel = document.getElementById('cluster-preparation');
    if (!panel) return;
    const message = document.getElementById('cluster-readiness-message');
    let expiry, sequence = 0;
    function unknown() {
        message.textContent = I18n.t('OS prerequisite checks are unavailable or expired. Protection is not confirmed.');
        panel.querySelectorAll('[data-cluster-check]').forEach(item => {
            item.textContent = I18n.t('Not confirmed'); item.dataset.state = 'unknown';
        });
    }
    async function refresh() {
        const current = ++sequence;
        try {
            const response = await fetch('/api/settings/cluster-readiness', { signal: AbortSignal.timeout(5000) });
            if (!response.ok) throw new Error();
            const state = await response.json();
            if (current !== sequence) return;
            clearTimeout(expiry);
            if (state.state !== 'preparation' || !(state.validFor > 0)) { unknown(); return; }
            message.textContent = I18n.t('Preparation only. Complete the pending checks before protected cluster activation.');
            const labels = { pass: 'Confirmed locally', blocked: 'Action required', pending: 'Still to verify', unknown: 'Not confirmed' };
            panel.querySelectorAll('[data-cluster-check]').forEach(item => {
                const status = state.checks?.[item.dataset.clusterCheck];
                item.textContent = I18n.t(labels[status] || labels.unknown);
                item.dataset.state = Object.hasOwn(labels, status) ? status : 'unknown';
            });
            expiry = setTimeout(unknown, Math.min(state.validFor, 90) * 1000);
        } catch { if (current === sequence) unknown(); }
    }
    window.addEventListener('proxypermit:connection', event => {
        if (event.detail.state === 'online') void refresh();
        else { sequence++; clearTimeout(expiry); unknown(); }
    });
    void refresh();
    setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
})();
