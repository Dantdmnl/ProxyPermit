'use strict';

(() => {
    const key = 'proxypermit-theme';
    const system = window.matchMedia('(prefers-color-scheme: dark)');
    const preferences = ['system', 'light', 'dark'];
    let preference = 'system';
    let toggle, select;
    try {
        const stored = localStorage.getItem(key);
        if (preferences.includes(stored)) preference = stored;
    } catch { /* The system preference still works without storage. */ }

    function apply() {
        const theme = preference === 'system'
            ? (system.matches ? 'dark' : 'light')
            : preference;
        document.documentElement.dataset.theme = theme;
        if (select) select.value = preference;
        toggle?.setAttribute('aria-pressed', String(theme === 'dark'));
    }

    function save(value) {
        preference = value;
        apply();
        try { localStorage.setItem(key, preference); } catch { /* Apply for this page. */ }
    }

    apply();
    system.addEventListener('change', apply);
    document.addEventListener('DOMContentLoaded', () => {
        select = document.getElementById('themePreference');
        if (select) {
            select.value = preference;
            select.addEventListener('change', () => save(select.value));
        }
        toggle = document.getElementById('themeToggle');
        if (toggle) {
            apply();
            toggle.hidden = false;
            toggle.addEventListener('click', () => {
                save(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
            });
        }
    });
})();
