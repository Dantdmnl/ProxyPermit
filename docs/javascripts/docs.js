/* Local documentation enhancements; image viewing is handled by Zensical. */
(() => {
    function init() {
        const article = document.querySelector('article');
        if (!article || article.querySelector('.docs-support')) return;
        const nl = location.pathname.includes('/nl/');
        const enlarge = nl ? 'Vergroten' : 'Enlarge';
        const zoomIn = nl ? 'Inzoomen' : 'Zoom in';
        const zoomOut = nl ? 'Uitzoomen' : 'Zoom out';
        for (const link of article.querySelectorAll('a.glightbox')) {
            const image = link.querySelector('img');
            if (!image) continue;
            const label = (nl ? 'Schermafbeelding vergroten: ' : 'Enlarge screenshot: ') + image.alt;
            link.setAttribute('aria-label', label);
            if (link.closest('.docs-figure')) {
                const badge = document.createElement('span');
                badge.className = 'docs-figure-enlarge';
                badge.setAttribute('aria-hidden', 'true');
                badge.textContent = enlarge;
                link.append(badge);
            }
        }
        // Add translated controls and predictable focus to the bundled lightbox.
        let opener, viewer, slideObserver, updateZoom;
        const desktopZoom = matchMedia('(min-width: 769px)');
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
                const tools = document.createElement('div');
                tools.className = 'docs-gallery-tools';
                const zoom = document.createElement('button');
                zoom.type = 'button';
                const original = document.createElement('a');
                original.textContent = nl ? 'Open origineel ↗' : 'Open original ↗';
                original.target = '_blank';
                original.rel = 'noopener';
                original.setAttribute('aria-label', nl ? 'Open origineel in nieuw tabblad' : 'Open original in a new tab');
                const activeImage = () => current.querySelector('.current .gslide-image img');
                updateZoom = () => {
                    const image = activeImage();
                    const zoomed = image?.closest('.gslide').classList.contains('zoomed');
                    const label = zoomed ? zoomOut : zoomIn;
                    if (zoom.textContent !== label) zoom.textContent = label;
                    zoom.hidden = !(desktopZoom.matches && image?.classList.contains('zoomable'));
                    if (zoom.hidden && document.activeElement === zoom) current.querySelector('.gclose')?.focus();
                    original.hidden = !image?.src;
                    if (image?.src && original.href !== image.src) original.href = image.src;
                };
                zoom.addEventListener('click', () => activeImage()?.click());
                tools.addEventListener('click', event => event.stopPropagation());
                tools.append(zoom, original);
                current.prepend(tools);
                slideObserver = new MutationObserver(updateZoom);
                slideObserver.observe(current.querySelector('.gslider'), {
                    subtree: true, childList: true, attributes: true, attributeFilter: ['class']
                });
                desktopZoom.addEventListener('change', updateZoom);
                updateZoom();
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
                    const controls = [...current.querySelectorAll('button:not(.disabled), .docs-gallery-tools a[href]')]
                        .filter(button => button.getClientRects().length);
                    if (!controls.length) return;
                    event.preventDefault(); event.stopPropagation();
                    const index = controls.indexOf(document.activeElement);
                    controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length].focus();
                });
                current.querySelector('.gclose')?.focus();
            } else if (!current && viewer) {
                viewer = null;
                slideObserver.disconnect();
                desktopZoom.removeEventListener('change', updateZoom);
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
