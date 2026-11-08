'use strict';

(() => {
    const button = document.querySelector('.shell-toggle');
    const mobile = window.matchMedia('(max-width: 650px)');
    const key = () => 'proxypermit-nav-' + (mobile.matches ? 'mobile' : 'desktop');

    function apply(expanded) {
        document.documentElement.classList.toggle('shell-collapsed', !expanded);
        button.setAttribute('aria-expanded', String(expanded));
        const label = expanded ? button.dataset.collapse : button.dataset.expand;
        button.setAttribute('aria-label', label);
        button.title = label;
    }

    function restore() {
        let saved = null;
        try { saved = localStorage.getItem(key()); } catch { /* Storage is optional. */ }
        apply(saved === null ? !mobile.matches : saved === 'expanded');
    }

    button.addEventListener('click', () => {
        const expanded = button.getAttribute('aria-expanded') === 'false';
        apply(expanded);
        try { localStorage.setItem(key(), expanded ? 'expanded' : 'collapsed'); } catch { /* Storage is optional. */ }
    });
    mobile.addEventListener('change', restore);
    window.addEventListener('pageshow', event => {
        if (event.persisted) window.location.reload();
    });

    if (window.location.pathname === '/') {
        const overview = document.querySelector('.shell-nav a[href="/"]');
        const rules = document.querySelector('.shell-nav a[href="/#filter-rules"]');
        const markLocation = () => {
            const onRules = window.location.hash === '#filter-rules';
            (onRules ? overview : rules).removeAttribute('aria-current');
            (onRules ? rules : overview).setAttribute('aria-current', onRules ? 'location' : 'page');
        };
        window.addEventListener('hashchange', markLocation);
        markLocation();
    }
    restore();
    button.hidden = false;
})();
