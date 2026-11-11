'use strict';

const form = document.getElementById('loginForm');
const username = document.getElementById('loginUsername');
const password = document.getElementById('loginPassword');
const otp = document.getElementById('loginOtp');
const otpGroup = document.getElementById('loginOtpGroup');
const feedback = document.getElementById('loginFeedback');
const button = document.getElementById('loginButton');
const languageLinks = document.querySelectorAll('.signin-languages a');
const reveal = document.getElementById('passwordReveal');
let buttonLabel = button.textContent;
let pending = false;
let changingLanguage = false;
const otpCells = document.querySelectorAll('.signin-code-cells span');

function renderOtp() {
    const current = Math.min(otp.selectionStart ?? otp.value.length, 5);
    otpCells.forEach((cell, index) => {
        cell.textContent = otp.value[index] || '';
        cell.toggleAttribute('data-current', index === current);
    });
}

otp.addEventListener('input', () => {
    otp.value = otp.value.replace(/[^0-9]/g, '').slice(0, 6);
    renderOtp();
});
for (const event of ['focus', 'click', 'keyup', 'select']) otp.addEventListener(event, renderOtp);
otp.addEventListener('paste', event => {
    const text = event.clipboardData?.getData('text') || '';
    const digits = text.replace(/[\s-]/g, '');
    if (!otp.readOnly && /^[0-9]{6}$/.test(digits)) {
        event.preventDefault();
        otp.value = digits;
        otp.dispatchEvent(new Event('input', { bubbles: true }));
    }
});

function showFeedback(message) {
    if (feedback.textContent !== message) feedback.textContent = message;
}

for (const link of languageLinks) {
    link.addEventListener('click', async event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (pending || changingLanguage || link.hasAttribute('aria-current')) return;
        changingLanguage = true;
        try {
            const response = await fetch(link.href, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
            const localized = new DOMParser().parseFromString(await response.text(), 'text/html');
            const translatedForm = localized.getElementById('loginForm');
            if (!translatedForm || localized.documentElement.lang !== link.lang) throw new Error();
            for (const selector of ['#signinTitle', '#loginOtpHint', 'label[for="loginUsername"]',
                'label[for="loginPassword"]', 'label[for="loginOtp"]']) {
                document.querySelector(selector).textContent = localized.querySelector(selector).textContent;
            }
            for (const key of ['missing', 'otp', 'failure', 'pending', 'languageFailure']) {
                if (feedback.textContent === form.dataset[key]) showFeedback(translatedForm.dataset[key]);
                form.dataset[key] = translatedForm.dataset[key];
            }
            for (const selector of ['.signin-languages', '#themeToggle', '#passwordReveal']) {
                const control = document.querySelector(selector);
                const translated = localized.querySelector(selector);
                for (const attribute of ['aria-label', 'title']) {
                    if (translated.hasAttribute(attribute)) control.setAttribute(attribute, translated.getAttribute(attribute));
                }
            }
            buttonLabel = localized.getElementById('loginButton').textContent;
            button.textContent = buttonLabel;
            const connection = document.getElementById('connectivityNotice');
            const translatedConnection = localized.getElementById('connectivityNotice');
            for (const key of ['offline', 'unavailable', 'restored', 'uncertain', 'checking', 'retry']) {
                connection.dataset[key] = translatedConnection.dataset[key];
            }
            for (const selector of ['[data-connection-detail]', '[data-connection-restored]',
                '[data-connection-uncertain]', '[data-connection-retry]', '[data-connection-dismiss]']) {
                connection.querySelector(selector).textContent = translatedConnection.querySelector(selector).textContent;
            }
            window.dispatchEvent(new Event('proxypermit:locale'));
            document.documentElement.lang = link.lang;
            document.title = localized.title;
            languageLinks.forEach(item => item.removeAttribute('aria-current'));
            link.setAttribute('aria-current', 'true');
            history.replaceState(history.state, '', link.href);
        } catch {
            showFeedback(form.dataset.languageFailure);
        } finally {
            changingLanguage = false;
        }
    });
}

reveal.hidden = false;
reveal.addEventListener('click', () => {
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    reveal.setAttribute('aria-pressed', String(visible));
});

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
        renderOtp();
        otp.removeAttribute('aria-invalid');
    }
});

form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending || changingLanguage) return;
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
