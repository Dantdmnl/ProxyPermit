'use strict';

(() => {
    const form = document.getElementById('haPreviewForm');
    if (!form) return;
    const button = form.querySelector('button[type=submit]');
    const feedback = document.getElementById('haPreviewFeedback');
    const result = document.getElementById('haPreviewResult');
    const firewall = document.getElementById('haFirewallPreview');
    const firewallRules = document.getElementById('haFirewallRules');
    const csrf = document.querySelector('meta[name="csrf-token"]').content;

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const fields = Object.fromEntries(new FormData(form));
        button.disabled = true;
        result.hidden = true;
        firewall.hidden = true;
        feedback.textContent = '';
        try {
            const response = await fetch('/api/settings/ha/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify(fields)
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error);
            const ha = data.topology;
            result.textContent = `sudo bash install.sh --plan --mode ha --ha-node ${ha.node} --ha-interface ${ha.interface} ` +
                `--ha-node-a ${ha.nodeA} --ha-node-b ${ha.nodeB} --ha-vip ${ha.vip} --ha-client-cidr ${ha.clients} --ha-vrid ${ha.vrid}`;
            result.hidden = false;
            firewallRules.textContent = data.firewallRules.map(rule =>
                'ufw ' + rule.slice(0, -1).join(' ') + " '" + rule.at(-1) + "'"
            ).join('\n');
            firewall.hidden = false;
            feedback.textContent = I18n.t('Het netwerkplan is geldig. Voer deze planopdracht alleen uit op een nieuwe host.');
        } catch (error) {
            feedback.textContent = error.message || I18n.t('Het HA-plan kon niet worden gecontroleerd.');
        } finally {
            button.disabled = false;
        }
    });
})();

(() => {
    const form = document.getElementById('haRetireForm');
    if (!form) return;
    const button = form.querySelector('button[type=submit]');
    const feedback = document.getElementById('haRetireFeedback');
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const fields = new FormData(form);
        const data = { password: fields.get('password'), otp: fields.get('otp') || '', confirmed: fields.has('confirmed') };
        form.reset();
        button.disabled = true;
        feedback.textContent = I18n.t('HA-node wordt uit bedrijf genomen…');
        try {
            const response = await fetch('/api/settings/ha/retire', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify(data)
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error);
            form.hidden = true;
            feedback.textContent = I18n.t('De HA-node is uit bedrijf genomen.');
        } catch (error) {
            button.disabled = false;
            feedback.textContent = error.message || I18n.t('De HA-node kon niet uit bedrijf worden genomen.');
        }
    });
})();
