'use strict';

const form = document.getElementById('loginForm');
const username = document.getElementById('loginUsername');
const password = document.getElementById('loginPassword');
const otp = document.getElementById('loginOtp');
const otpGroup = document.getElementById('loginOtpGroup');
const feedback = document.getElementById('loginFeedback');
const button = document.getElementById('loginButton');
const languageLinks = document.querySelectorAll('.signin-languages a');
const buttonLabel = button.textContent;
let pending = false;

function showFeedback(message) {
    if (feedback.textContent !== message) feedback.textContent = message;
}

for (const link of languageLinks) {
    link.addEventListener('click', event => {
        if (pending) event.preventDefault();
    });
}

function setPending(value) {
    pending = value;
    button.disabled = value;
    button.textContent = value ? form.dataset.pending : buttonLabel;
    for (const field of [username, password, otp]) field.readOnly = value;
    for (const link of languageLinks) {
        if (value) link.setAttribute('aria-disabled', 'true');
        else link.removeAttribute('aria-disabled');
    }
}

form.addEventListener('input', event => {
    event.target.removeAttribute('aria-invalid');
    showFeedback('');
    if (event.target === username || event.target === password) {
        otpGroup.hidden = true;
        otp.disabled = true;
        otp.value = '';
        otp.removeAttribute('aria-invalid');
    }
});

form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending) return;
    const user = username.value.trim();
    if (!user || !password.value) {
        if (!user) username.setAttribute('aria-invalid', 'true');
        if (!password.value) password.setAttribute('aria-invalid', 'true');
        showFeedback(form.dataset.missing);
        (user ? password : username).focus();
        return;
    }
    if (!otp.disabled && !/^[0-9]{6}$/.test(otp.value.trim())) {
        otp.setAttribute('aria-invalid', 'true');
        showFeedback(form.dataset.otp);
        otp.focus();
        return;
    }

    const credentials = { username: user, password: password.value, otp: otp.value.trim() };
    setPending(true);
    let nextField;
    let signedIn = false;
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(credentials),
            signal: AbortSignal.timeout(8000)
        });
        const result = await response.json();
        if (result.otpRequired) {
            otpGroup.hidden = false;
            otp.disabled = false;
            showFeedback('');
            nextField = otp;
        } else if (response.ok && result.success) {
            signedIn = true;
            if (window.location.pathname === form.dataset.destination) {
                window.location.reload();
            } else {
                window.location.replace(form.dataset.destination);
            }
        } else {
            showFeedback(result.error || form.dataset.failure);
            nextField = otp.disabled ? password : otp;
            nextField.select();
        }
    } catch {
        showFeedback(form.dataset.failure);
    } finally {
        if (!signedIn) {
            setPending(false);
            nextField?.focus();
        }
    }
});

button.disabled = false;
window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
});
