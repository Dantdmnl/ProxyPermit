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
    const mode = document.getElementById('haSetupMode');
    const confirm = document.getElementById('haSetupConfirm');
    const dialog = document.getElementById('haSetupDialog');
    let reviewed = null;
    let working = false;

    function invalidate() {
        reviewed = null;
        if (confirm) confirm.hidden = true;
    }
    form.addEventListener('input', invalidate);
    form.addEventListener('change', invalidate);
    const networkSelect = document.getElementById('haDetectedNetwork');
    const networkHelp = document.getElementById('haNetworkHelp');
    let networks = [];
    let networkBlocked = false;
    const automatic = {};
    function fillSuggested(name, value, replace = false) {
        const input = form.elements.namedItem(name);
        if (value && (replace || !input.value || input.value === automatic[name])) {
            input.value = value;
            automatic[name] = value;
            invalidate();
        }
    }
    function chooseNetwork(network, replace = true) {
        if (!network) return;
        fillSuggested('interface', network.interface, replace);
        if (mode?.value !== 'join') {
            const local = form.elements.namedItem('node').value === 'b' ? 'nodeB' : 'nodeA';
            fillSuggested(local, network.address, replace);
            if (!network.dynamic) {
                fillSuggested('clients', network.network);
                const other = form.elements.namedItem(local === 'nodeA' ? 'nodeB' : 'nodeA').value;
                if (network.suggestedVip?.split('/')[0] !== other) fillSuggested('vip', network.suggestedVip);
            } else {
                for (const name of ['clients', 'vip']) {
                    const input = form.elements.namedItem(name);
                    if (input.value === automatic[name]) input.value = '';
                    delete automatic[name];
                }
            }
        }
        networkBlocked = network.dynamic;
        button.disabled = working || networkBlocked;
        networkHelp.dataset.warning = String(network.dynamic);
        networkHelp.textContent = I18n.t(network.dynamic
            ? 'Dit adres is dynamisch. Stel eerst een vast adres in op de host.'
            : 'Interface, lokaal adres en subnet zijn ingevuld. Controleer het clientnetwerk en reserveer het virtuele IP.');
    }
    function matchJoinInterface() {
        if (mode?.value !== 'join') return;
        try {
            const invite = JSON.parse(document.getElementById('haJoinCode').value);
            const matches = networks.filter(network => network.address === invite.topology?.nodeB);
            if (matches.length === 1) {
                networkSelect.value = String(networks.indexOf(matches[0]));
                chooseNetwork(matches[0]);
            }
        } catch { /* Server-side validation remains authoritative. */ }
    }
    networkSelect.addEventListener('change', () => {
        if (networkSelect.value !== '') chooseNetwork(networks[Number(networkSelect.value)]);
        else {
            networkBlocked = false;
            button.disabled = working;
        }
    });
    document.getElementById('haJoinCode')?.addEventListener('input', matchJoinInterface);
    form.elements.namedItem('node').addEventListener('change', () => {
        if (networkSelect.value !== '') chooseNetwork(networks[Number(networkSelect.value)]);
    });
    let detected = false;
    const refreshNetworks = document.getElementById('haRefreshNetworks');
    async function loadNetworks() {
        if (refreshNetworks.disabled) return;
        refreshNetworks.disabled = true;
        detected = true;
        const previous = networkSelect.value !== '' ? networks[Number(networkSelect.value)]?.interface : undefined;
        try {
            const response = await fetch('/api/settings/ha/network', { cache: 'no-store' });
            if (!response.ok) throw new Error();
            networks = (await response.json()).networks;
            const options = document.getElementById('haInterfaceOptions');
            networkSelect.options.length = 1;
            options.replaceChildren();
            networkSelect.disabled = false;
            for (const [index, network] of networks.entries()) {
                networkSelect.add(new Option(`${network.interface} · ${network.address}/${network.prefix}` +
                    (network.dynamic ? ' · DHCP' : ''), String(index)));
                if (![...options.options].some(option => option.value === network.interface)) {
                    options.append(new Option(network.interface, network.interface));
                }
            }
            if (!networks.length) throw new Error();
            networkHelp.dataset.warning = 'false';
            networkHelp.textContent = I18n.t('Kies het netwerk waarop de twee HA-nodes met elkaar communiceren.');
            const matching = networks.findIndex(network => network.interface === previous);
            if (matching >= 0 || (networks.length === 1 && ['interface', 'nodeA', 'nodeB', 'vip', 'clients']
                .every(name => !form.elements.namedItem(name).value))) {
                const selected = matching >= 0 ? matching : 0;
                networkSelect.value = String(selected);
                chooseNetwork(networks[selected], false);
            }
            matchJoinInterface();
        } catch {
            networkBlocked = false;
            button.disabled = working;
            networkSelect.disabled = true;
            networkHelp.textContent = I18n.t('Network detection is unavailable. Enter the network settings manually.');
        } finally { refreshNetworks.disabled = false; }
    }
    refreshNetworks.addEventListener('click', loadNetworks);
    form.closest('details').addEventListener('toggle', event => {
        if (event.target.open && !detected) loadNetworks();
    });
    if (mode) mode.addEventListener('change', () => {
        const joining = mode.value === 'join';
        invalidate();
        result.hidden = true;
        firewall.hidden = true;
        feedback.textContent = '';
        document.getElementById('haJoinCodeLabel').hidden = !joining;
        document.getElementById('haJoinCode').disabled = !joining;
        for (const name of ['nodeA', 'nodeB', 'vip', 'clients', 'vrid']) {
            const input = form.elements.namedItem(name);
            input.disabled = joining;
            input.closest('label').hidden = joining;
        }
        matchJoinInterface();
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (working || networkBlocked) return;
        const fields = Object.fromEntries(new FormData(form));
        if (mode?.value === 'join') delete fields.node;
        button.disabled = true;
        result.hidden = true;
        firewall.hidden = true;
        feedback.textContent = '';
        invalidate();
        try {
            const response = await fetch('/api/settings/ha/preview', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify(fields)
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error);
            const ha = data.topology;
            if (data.canApply) {
                reviewed = { plan: fields, revision: data.revision };
                result.textContent = [
                    I18n.t('Node') + ': ' + ha.node.toUpperCase(),
                    I18n.t('Netwerkinterface') + ': ' + ha.interface,
                    I18n.t('Virtueel IP') + ': ' + ha.vip,
                    I18n.t('IPv4-adres van node A') + ': ' + ha.nodeA,
                    I18n.t('IPv4-adres van node B') + ': ' + ha.nodeB
                ].join('\n');
                confirm.hidden = false;
                feedback.textContent = I18n.t('Controleer het plan en bevestig daarna de omzetting.');
            } else {
                result.textContent = `sudo bash install.sh --plan --mode ha --ha-node ${ha.node} --ha-interface ${ha.interface} ` +
                    `--ha-node-a ${ha.nodeA} --ha-node-b ${ha.nodeB} --ha-vip ${ha.vip} --ha-client-cidr ${ha.clients} --ha-vrid ${ha.vrid}`;
                feedback.textContent = I18n.t('Het netwerkplan is geldig. Voer deze planopdracht alleen uit op een nieuwe host.');
            }
            result.hidden = false;
            firewallRules.textContent = data.firewallRules.map(rule =>
                'ufw ' + rule.slice(0, -1).join(' ') + " '" + rule.at(-1) + "'"
            ).join('\n');
            firewall.hidden = false;
        } catch (error) {
            feedback.textContent = error.message || I18n.t('Het HA-plan kon niet worden gecontroleerd.');
        } finally { button.disabled = working || networkBlocked; }
    });
    if (!dialog) return;
    const applyForm = document.getElementById('haSetupApplyForm');
    const error = document.getElementById('haSetupError');
    confirm.addEventListener('click', () => {
        if (!reviewed || working) return;
        applyForm.reset();
        error.textContent = '';
        dialog.showModal();
    });
    async function poll() {
        try {
            const response = await fetch('/api/settings/ha/setup-status', { cache: 'no-store' });
            if (response.status === 401) { window.location.reload(); return; }
            if (!response.ok) throw new Error();
            const status = await response.json();
            if (status.phase === 'complete') {
                if (working) window.location.reload();
                return;
            }
            if (status.phase === 'failed') {
                working = false;
                button.disabled = false;
                feedback.textContent = I18n.t(status.error || 'HA setup failed. Check the host service log.');
                invalidate();
                return;
            }
            if (!['queued', 'running', 'activating'].includes(status.phase)) return;
            working = true;
            button.disabled = true;
            feedback.textContent = I18n.t('HA wordt ingesteld. De beheerconsole wordt opnieuw gestart.');
        } catch {
            feedback.textContent = I18n.t('HA wordt ingesteld. De beheerconsole wordt opnieuw gestart.');
        }
        window.setTimeout(poll, 2000);
    }
    applyForm.addEventListener('submit', async event => {
        if (event.submitter?.value === 'cancel') return;
        event.preventDefault();
        if (!reviewed || working) return;
        const password = document.getElementById('haSetupPassword').value;
        const otp = document.getElementById('haSetupOtp')?.value || '';
        const controls = applyForm.querySelectorAll('button');
        controls.forEach(control => { control.disabled = true; });
        try {
            const response = await fetch('/api/settings/ha/setup', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ ...reviewed, password, otp, confirmed: true })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error);
            working = true;
            dialog.close();
            applyForm.reset();
            confirm.hidden = true;
            poll();
        } catch (failure) {
            error.textContent = failure.message || I18n.t('HA setup could not be started. Check the host service log.');
            document.getElementById('haSetupPassword').value = '';
        } finally {
            controls.forEach(control => { control.disabled = false; });
        }
    });
    poll();
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
