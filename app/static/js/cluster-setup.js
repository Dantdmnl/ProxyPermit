'use strict';
(() => {
    const panel = document.getElementById('cluster-preparation');
    const migration = document.getElementById('cluster-migration');
    if (migration) {
        const message = document.getElementById('cluster-migration-phase');
        const labels = {
            reserving: 'Reservation interrupted or in progress. Review both hosts before resuming.',
            reserved: 'Local VIP reserved. Confirm the peer reservation before preparing transport.',
            transport: 'Local cluster transport prepared. Confirm both hosts before starting it.',
            running: 'Cluster services started. Verify membership before configuring the VIP.',
            configured: 'VIP configuration staged. Verify quorum and fencing before activation.',
            activating: 'Activation requested. Check both hosts before retrying.',
            active: 'Migration completed on this node. Check the live failover protection status.'
        };
        let refreshSequence = 0;
        let expiry;
        function unknownPhase() {
            clearTimeout(expiry);
            message.textContent = I18n.t('The local migration phase could not be confirmed.');
        }
        async function refreshMigration() {
            const current = ++refreshSequence;
            try {
                const response = await fetch('/api/settings/cluster-readiness', { signal: AbortSignal.timeout(5000) });
                if (!response.ok) throw new Error();
                const state = await response.json();
                if (current !== refreshSequence) return;
                if (state.state !== 'migration' || !Object.hasOwn(labels, state.phase) || !(state.validFor > 0)) {
                    unknownPhase();
                    return;
                }
                clearTimeout(expiry);
                message.textContent = I18n.t(labels[state.phase]);
                expiry = setTimeout(unknownPhase, Math.min(state.validFor, 20) * 1000);
            } catch {
                if (current === refreshSequence) unknownPhase();
            }
        }
        window.addEventListener('proxypermit:connection', event => {
            if (event.detail.state === 'online') void refreshMigration();
            else { refreshSequence++; unknownPhase(); }
        });
        void refreshMigration();
        setInterval(() => { if (!document.hidden) void refreshMigration(); }, 10000);
        return;
    }
    if (!panel) return;
    const message = document.getElementById('cluster-readiness-message');
    const form = document.getElementById('cluster-plan-form');
    const feedback = document.getElementById('cluster-plan-feedback');
    const result = document.getElementById('cluster-plan-result');
    const download = document.getElementById('cluster-plan-download');
    const fingerprint = document.getElementById('cluster-plan-fingerprint');
    const firewall = document.getElementById('cluster-plan-firewall');
    const fileInput = document.getElementById('cluster-plan-file');
    const submit = form.querySelector('button[type="submit"]');
    const stage = document.getElementById('cluster-plan-stage');
    const prepare = document.getElementById('cluster-plan-prepare');
    const loadSaved = document.getElementById('cluster-plan-load');
    const jobStatus = document.getElementById('cluster-plan-job-status');
    const savedPlan = document.getElementById('cluster-local-saved');
    let reviewed, readiness, waitingFor = '', waitingUntil = 0, submitting = false;
    let planSequence = 0;
    let expiry, sequence = 0;

    function updateControls() {
        if (!stage) return;
        const revision = readiness?.planRevision;
        const busy = submitting || Date.now() < waitingUntil || readiness?.job?.phase === 'running';
        stage.disabled = busy || !readiness?.webAvailable || !reviewed?.canStage || readiness.planLocked === true
            || typeof revision !== 'string' || reviewed.planHash === revision;
        prepare.disabled = busy || !readiness?.webAvailable || !revision || readiness.packagesPrepared === true
            || readiness.checks?.time !== 'pass';
        savedPlan.hidden = !revision;
        savedPlan.querySelector('code').textContent = revision || '';
        loadSaved.hidden = !readiness?.stagedPlan;
    }

    function updateJob(state) {
        if (!jobStatus || !state.webAvailable) return;
        const job = state.job;
        if (waitingFor && job?.id === waitingFor && job.phase !== 'running') {
            waitingFor = '';
            waitingUntil = 0;
        }
        if (Date.now() < waitingUntil && (!waitingFor || job?.id !== waitingFor)) return;
        if (!job) return;
        const labels = {
            running: 'Local cluster preparation is running.',
            failed: 'Local cluster preparation stopped. Check the plan and the host service log.',
            complete: job.action === 'stage' ? 'The plan is saved on this node.' : 'Cluster packages are prepared on this node.'
        };
        if (Object.hasOwn(labels, job.phase)) jobStatus.textContent = I18n.t(labels[job.phase]);
    }

    function clearPlan() {
        planSequence++;
        reviewed = undefined;
        updateControls();
        result.hidden = true;
        submit.disabled = false;
        feedback.textContent = '';
        delete feedback.dataset.error;
        if (download.hasAttribute('href')) URL.revokeObjectURL(download.href);
        download.removeAttribute('href');
    }

    function showError(message) {
        feedback.dataset.error = '';
        feedback.textContent = I18n.t(message);
    }

    function fillPlan(plan) {
        for (const [index, side] of ['A', 'B'].entries()) {
            form.elements.namedItem(`node${side}Name`).value = plan.nodes[index].name;
            form.elements.namedItem(`node${side}Target`).value = plan.nodes[index].target;
        }
        form.elements.namedItem('witness').value = plan.witness;
        form.elements.namedItem('fenceAddress').value = plan.fenceAddress;
    }

    loadSaved?.addEventListener('click', () => {
        if (!readiness?.stagedPlan) return;
        clearPlan();
        fillPlan(readiness.stagedPlan);
        form.elements.namedItem('nodeAName').focus();
    });

    form.addEventListener('input', clearPlan);
    form.addEventListener('submit', event => {
        event.preventDefault();
        clearPlan();
        void validatePlan(Object.fromEntries(new FormData(form)), planSequence);
    });

    fileInput.addEventListener('change', async () => {
        const file = fileInput.files[0];
        fileInput.value = '';
        if (!file) return;
        clearPlan();
        const current = planSequence;
        const invalid = 'Choose a valid Proxmox cluster-plan.json of up to 4 KiB.';
        try {
            if (file.size > 4096) { showError(invalid); return; }
            const plan = JSON.parse(await file.text());
            if (current !== planSequence) return;
            if (plan?.version !== 1 || plan.platform !== 'proxmox' || Object.keys(plan).length !== 5
                    || typeof plan.witness !== 'string' || typeof plan.fenceAddress !== 'string'
                    || !Array.isArray(plan.nodes) || plan.nodes.length !== 2
                    || !plan.nodes.every(node => node && Object.keys(node).length === 3
                        && ['name', 'address', 'target'].every(key => typeof node[key] === 'string'))) {
                showError(invalid);
                return;
            }
            await validatePlan({ plan }, current);
        } catch {
            if (current === planSequence) showError(invalid);
        }
    });

    async function validatePlan(payload, current) {
        submit.disabled = true;
        try {
            const response = await fetch('/api/settings/cluster-plan/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': form.dataset.csrf },
                body: JSON.stringify(payload),
                signal: AbortSignal.timeout(5000)
            });
            const data = await response.json();
            if (current !== planSequence) return;
            if (!response.ok || !data.plan || !data.planHash) {
                showError(data.error || 'Could not validate the cluster plan. Try again.');
                return;
            }
            if (payload.plan) fillPlan(data.plan);
            download.href = URL.createObjectURL(new Blob([JSON.stringify(data.plan, null, 2) + '\n'],
                { type: 'application/json' }));
            fingerprint.textContent = data.planHash;
            firewall.hidden = !data.firewallPreview?.length;
            firewall.querySelector('code').textContent = (data.firewallPreview || []).join('\n');
            reviewed = data;
            updateControls();
            feedback.textContent = I18n.t('Plan validated and ready to download.');
            result.hidden = false;
        } catch {
            if (current === planSequence) showError('Could not validate the cluster plan. Try again.');
        } finally {
            if (current === planSequence) submit.disabled = false;
        }
    }

    if (stage) {
        const dialog = document.getElementById('cluster-plan-dialog');
        const jobForm = document.getElementById('cluster-plan-job-form');
        const error = document.getElementById('cluster-job-error');
        const confirm = document.getElementById('cluster-plan-job-confirm');
        let action;
        const labels = {
            stage: ['Save plan on this node', 'Save the reviewed plan in the local system configuration.'],
            prepare: ['Prepare cluster packages', 'Install the fixed cluster dependencies on this node. Keepalived continues to serve the existing HA setup.']
        };
        const open = next => {
            action = next;
            jobForm.reset();
            error.textContent = '';
            const [title, summary] = labels[next].map(text => I18n.t(text));
            document.getElementById('cluster-job-title').textContent = title;
            document.getElementById('cluster-job-summary').textContent = summary;
            confirm.textContent = title;
            dialog.showModal();
            jobForm.elements.password.focus();
        };
        stage.addEventListener('click', () => open('stage'));
        prepare.addEventListener('click', () => open('prepare'));
        protectDialogDismiss(dialog, [document.getElementById('cluster-plan-job-cancel')],
            () => Boolean(jobForm.elements.password.value || jobForm.elements.otp?.value), () => submitting);
        dialog.addEventListener('close', () => jobForm.reset());
        jobForm.addEventListener('submit', async event => {
            event.preventDefault();
            if (submitting || (action === 'stage' ? stage.disabled : prepare.disabled)) return;
            submitting = true;
            confirm.disabled = true;
            error.textContent = '';
            updateControls();
            try {
                const response = await fetch('/api/settings/cluster-plan/jobs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': form.dataset.csrf },
                    body: JSON.stringify({ action, confirmed: true, planHash: action === 'stage' ? reviewed.planHash : readiness.planRevision,
                        ...(action === 'stage' ? { plan: reviewed.plan } : {}),
                        password: jobForm.elements.password.value, otp: jobForm.elements.otp?.value || '' }),
                    signal: AbortSignal.timeout(12000)
                });
                const reply = await response.json();
                if (!response.ok) {
                    error.textContent = I18n.t(reply.error || 'Local cluster preparation could not be started. Check the system service.');
                    return;
                }
                waitingFor = reply.id;
                waitingUntil = Date.now() + (action === 'stage' ? 20000 : 660000);
                jobStatus.textContent = I18n.t('Local cluster preparation is queued.');
                dialog.close();
            } catch {
                waitingFor = '';
                waitingUntil = Date.now() + 15000;
                jobStatus.textContent = I18n.t('No reply was received. Check the local job status before retrying.');
                dialog.close();
            } finally {
                jobForm.elements.password.value = '';
                if (jobForm.elements.otp) jobForm.elements.otp.value = '';
                submitting = false;
                confirm.disabled = false;
                updateControls();
                void refresh();
            }
        });
    }
    function unknown() {
        readiness = undefined;
        updateControls();
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
            readiness = state;
            updateJob(state);
            updateControls();
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
        else { sequence++; clearTimeout(expiry); unknown(); clearPlan(); }
    });
    void refresh();
    setInterval(() => { if (!document.hidden) void refresh(); }, stage ? 4000 : 15000);
})();
