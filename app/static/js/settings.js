'use strict';

(() => {
    const dialog = document.getElementById('accountDialog');
    if (!dialog) return;
    const $ = id => document.getElementById(id);
    const form = $('accountForm');
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    const hasFactor = dialog.dataset.mfa === 'true';
    const closeButtons = [...dialog.querySelectorAll('[data-close-account]')];
    let action, target, busy = false;

    function field(group, id, visible) {
        $(group).hidden = !visible;
        $(id).required = visible;
    }

    document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => {
        action = button.dataset.action;
        target = button.dataset.username;
        form.reset();
        $('accountError').textContent = '';
        $('accountDialogTitle').textContent = button.textContent.trim();
        $('accountTarget').hidden = !target;
        $('accountTarget').textContent = target || '';
        field('accountNameGroup', 'accountName', action === 'create');
        field('newPasswordGroup', 'newAccountPassword', action === 'create' || action === 'password');
        field('currentPasswordGroup', 'currentAccountPassword', true);
        field('accountOtpGroup', 'accountOtp', hasFactor);
        $('enrollGroup').hidden = true;
        dialog.showModal();
        $(action === 'create' ? 'accountName' : 'currentAccountPassword').focus();
    }));
    protectDialogDismiss(dialog, closeButtons, () =>
        action === 'totp-confirm' || [...form.querySelectorAll('input:not([readonly])')].some(input => input.value), () => busy);
    dialog.addEventListener('close', () => {
        if (dialog.open) return;
        form.reset();
        $('enrollmentQr').removeAttribute('src');
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        busy = true;
        $('accountSubmit').disabled = true;
        closeButtons.forEach(button => { button.disabled = true; });
        $('accountError').textContent = '';
        try {
            const response = await fetch('/api/settings/accounts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({
                    action, username: target || $('accountName').value.trim(),
                    password: $('currentAccountPassword').value, newPassword: $('newAccountPassword').value,
                    otp: $('accountOtp').value.trim(), code: $('accountOtp').value.trim()
                })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || I18n.t('Onbekende fout'));
            if (action === 'totp-start') {
                action = 'totp-confirm';
                $('currentAccountPassword').value = '';
                field('currentPasswordGroup', 'currentAccountPassword', false);
                field('accountOtpGroup', 'accountOtp', true);
                $('enrollmentSecret').value = result.secret;
                $('enrollmentQr').src = result.qr;
                $('enrollGroup').hidden = false;
                $('enrollmentSecret').focus();
                $('enrollmentSecret').select();
            } else {
                window.location.reload();
            }
        } catch (error) {
            $('accountError').textContent = error.message;
        } finally {
            busy = false;
            $('accountSubmit').disabled = false;
            closeButtons.forEach(button => { button.disabled = false; });
        }
    });
})();
