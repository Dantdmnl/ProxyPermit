'use strict';

(() => {
    const key = 'proxypermit-theme';
    const system = window.matchMedia('(prefers-color-scheme: dark)');
    const preferences = ['system', 'light', 'dark'];
    let preference = 'system';
    try {
        const stored = localStorage.getItem(key);
        if (preferences.includes(stored)) preference = stored;
    } catch { /* The system preference still works without storage. */ }

    function apply() {
        document.documentElement.dataset.theme = preference === 'system'
            ? (system.matches ? 'dark' : 'light')
            : preference;
    }

    apply();
    system.addEventListener('change', apply);
    document.addEventListener('DOMContentLoaded', () => {
        const select = document.getElementById('themePreference');
        if (!select) return;
        select.value = preference;
        select.addEventListener('change', () => {
            preference = select.value;
            apply();
            try { localStorage.setItem(key, preference); } catch { /* Apply for this page. */ }
        });
    });
})();
