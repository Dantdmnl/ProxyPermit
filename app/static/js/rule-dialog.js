'use strict';

(() => {
    const dialog = document.getElementById('ruleDialog');
    const form = document.getElementById('addEntryForm');
    const opener = document.getElementById('openRuleDialog');
    const type = document.getElementById('entryType');
    const fields = document.getElementById('ruleFields');
    const preview = document.getElementById('regexPreview');
    const saveError = document.getElementById('ruleSaveError');
    const retry = document.getElementById('retryRuleValidation');
    const submit = document.getElementById('submitButton');
    const closeButtons = [...dialog.querySelectorAll('[data-close-rule]')];
    const inputFor = {
        domain: document.getElementById('domainInput'),
        regex: document.getElementById('regexInput'),
        comment: document.getElementById('commentInput')
    };
    let sequence = 0, timer, saving = false, valid = false;

    function feedback(state, message, pattern) {
        preview.dataset.state = state;
        preview.textContent = message;
        if (pattern) {
            const code = document.createElement('code');
            code.textContent = pattern;
            preview.append(code);
        }
    }

    function updateControls() {
        submit.disabled = saving || !valid;
        fields.disabled = saving;
        closeButtons.forEach(button => { button.disabled = saving; });
    }

    async function post(url, payload) {
        const response = await fetchWithTimeout(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await parseJsonSafe(response);
        if (!response.ok || !data) {
            throw new Error(data?.error || I18n.t('Onbekende fout'));
        }
        return data;
    }

    function scheduleValidation() {
        clearTimeout(timer);
        const version = ++sequence;
        const kind = type.value;
        const input = inputFor[kind];
        const content = input.value.trim();
        valid = false;
        retry.hidden = true;
        input.removeAttribute('aria-invalid');
        saveError.textContent = '';
        updateControls();
        if (!content) {
            feedback('', '');
            return;
        }
        feedback('pending', I18n.t('Regel controleren…'));
        timer = setTimeout(async () => {
            try {
                const result = await post('/api/validate', { content, is_comment: kind === 'comment' });
                if (version !== sequence) return;
                valid = result.valid && result.entry_type === kind;
                if (valid) {
                    feedback('valid', I18n.t(result.generated_regex ? 'Geldig. Patroon bij opslaan' : 'Regel is geldig.'), result.generated_regex);
                } else {
                    input.setAttribute('aria-invalid', 'true');
                    feedback('error', result.error || I18n.t('De invoer past niet bij het gekozen regeltype.'));
                }
            } catch (error) {
                if (version !== sequence) return;
                feedback('error', error.message);
                retry.hidden = false;
            }
            updateControls();
        }, 250);
    }

    function showType() {
        for (const [kind, input] of Object.entries(inputFor)) {
            input.closest('.form-group').hidden = kind !== type.value;
            input.removeAttribute('aria-invalid');
        }
        document.getElementById('descriptionGroup').hidden = type.value === 'comment';
        document.getElementById('restartGroup').hidden = type.value === 'comment';
        scheduleValidation();
    }

    opener.addEventListener('click', () => {
        showType();
        dialog.showModal();
        inputFor[type.value].focus();
    });
    protectDialogDismiss(dialog, closeButtons, () =>
        [...Object.values(inputFor), document.getElementById('descriptionInput')].some(input => input.value.trim()), () => saving);
    dialog.addEventListener('close', () => {
        if (dialog.open) return;
        clearTimeout(timer);
        sequence++;
        form.reset();
        opener.focus();
    });
    type.addEventListener('change', showType);
    retry.addEventListener('click', () => {
        inputFor[type.value].focus();
        scheduleValidation();
    });
    Object.values(inputFor).forEach(input => input.addEventListener('input', scheduleValidation));

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!valid || saving) return;
        const comment = type.value === 'comment';
        saving = true;
        updateControls();
        submit.textContent = I18n.t('Opslaan…');
        try {
            const result = await post('/api/entries', {
                content: inputFor[type.value].value.trim(),
                is_comment: comment,
                auto_restart: !comment && document.getElementById('autoRestart').checked,
                description: comment ? null : document.getElementById('descriptionInput').value.trim() || null
            });
            if (!result.success) throw new Error(result.error || I18n.t('Onbekende fout'));
            form.reset();
            valid = false;
            dialog.close();
            showToast('success', I18n.t('Toegevoegd'), I18n.t('Regel opgeslagen.'));
            if (result.restart_queued) {
                pollTinyproxyStatus(10, I18n.t('Config herladen'), I18n.t('Tinyproxy config is herladen'));
            }
            loadEntriesDebounced(0);
        } catch (error) {
            saveError.textContent = error.message;
        } finally {
            saving = false;
            updateControls();
            submit.textContent = I18n.t('Regel opslaan');
        }
    });
})();
