/* Local documentation enhancements; image viewing is handled by Zensical. */
(() => {
    function init() {
        const article = document.querySelector('article');
        if (!article || article.querySelector('.docs-support')) return;
        const nl = location.pathname.includes('/nl/');
        for (const link of article.querySelectorAll('a.glightbox')) {
            const image = link.querySelector('img');
            if (!image) continue;
            const label = (nl ? 'Schermafbeelding vergroten: ' : 'Enlarge screenshot: ') + image.alt;
            link.setAttribute('aria-label', label);
            link.title = nl ? 'Klik om te vergroten' : 'Click to enlarge';
        }
        // Add translated controls and predictable focus to the bundled lightbox.
        let opener, viewer;
        article.addEventListener('click', event => {
            const link = event.target.closest('a.glightbox');
            if (link) opener = link;
        }, true);
        new MutationObserver(() => {
            const current = document.getElementById('glightbox-body');
            if (current && current !== viewer) {
                viewer = current;
                current.setAttribute('aria-modal', 'true');
                current.setAttribute('aria-label', nl ? 'Schermafbeeldingen' : 'Screenshots');
                for (const [selector, label] of [
                    ['.gclose', nl ? 'Sluiten' : 'Close'],
                    ['.gprev', nl ? 'Vorige afbeelding' : 'Previous image'],
                    ['.gnext', nl ? 'Volgende afbeelding' : 'Next image']
                ]) {
                    const button = current.querySelector(selector);
                    button?.setAttribute('aria-label', label);
                    if (button) button.title = label;
                }
                current.addEventListener('keydown', event => {
                    if (event.key !== 'Tab') return;
                    const controls = [...current.querySelectorAll('button:not(.disabled)')]
                        .filter(button => button.getClientRects().length);
                    if (!controls.length) return;
                    event.preventDefault(); event.stopPropagation();
                    const index = controls.indexOf(document.activeElement);
                    controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length].focus();
                });
                current.querySelector('.gclose')?.focus();
            } else if (!current && viewer) {
                viewer = null;
                opener?.focus({ preventScroll: true });
            }
        }).observe(document.body, { childList: true });
        const support = document.createElement('aside'); support.className = 'docs-support';
        const text = document.createElement('p'); text.textContent = nl ? 'Heb je iets aan ProxyPermit? Met een ster op GitHub help je anderen het project te vinden.' : 'Finding ProxyPermit useful? A star on GitHub helps others discover the project.';
        const link = document.createElement('a'); link.href = 'https://github.com/Dantdmnl/ProxyPermit'; link.textContent = nl ? '☆ Geef ProxyPermit een ster op GitHub' : '☆ Star ProxyPermit on GitHub';
        support.append(text,link); article.append(support);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
