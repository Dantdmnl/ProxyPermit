/* Local, accessible screenshot viewer. No external scripts or image services. */
(() => {
    function init() {
        const article = document.querySelector('article');
        if (!article || document.getElementById('docs-image-viewer')) return;
        const nl = location.pathname.includes('/nl/');
        const label = nl ? {open:'Schermafbeelding vergroten',close:'Sluiten',zoom:'Inzoomen',fit:'Passend maken'} : {open:'Enlarge screenshot',close:'Close',zoom:'Zoom in',fit:'Fit image'};
        const dialog = document.createElement('dialog');
        dialog.id = 'docs-image-viewer'; dialog.className = 'docs-image-viewer';
        dialog.setAttribute('aria-labelledby', 'docs-image-caption');
        const toolbar = document.createElement('div'); toolbar.className = 'docs-image-toolbar';
        const zoom = document.createElement('button'); zoom.type = 'button'; zoom.textContent = label.zoom; zoom.setAttribute('aria-pressed','false');
        const close = document.createElement('button'); close.type = 'button'; close.textContent = label.close; close.autofocus = true;
        toolbar.append(zoom, close);
        const viewport = document.createElement('div'); viewport.className = 'docs-image-viewport'; viewport.tabIndex = 0;
        const image = document.createElement('img');
        viewport.append(image);
        const caption = document.createElement('p'); caption.id = 'docs-image-caption';
        dialog.append(toolbar, viewport, caption); document.body.append(dialog);
        let opener;
        function fit() { dialog.classList.remove('is-zoomed'); image.style.width = ''; zoom.textContent = label.zoom; zoom.setAttribute('aria-pressed','false'); }
        close.addEventListener('click', () => dialog.close());
        zoom.addEventListener('click', () => {
            if (dialog.classList.contains('is-zoomed')) fit();
            else { dialog.classList.add('is-zoomed'); image.style.width = Math.max(image.naturalWidth, viewport.clientWidth * 1.5) + 'px'; zoom.textContent = label.fit; zoom.setAttribute('aria-pressed','true'); }
        });
        dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
        dialog.addEventListener('close', () => { document.body.classList.remove('docs-image-open'); fit(); image.removeAttribute('src'); opener?.focus(); });
        for (const source of article.querySelectorAll('img[src]')) {
            if (!new URL(source.src).pathname.match(/\/assets\/(en|nl)\//) || source.closest('a')) continue;
            const button = document.createElement('button'); button.type = 'button'; button.className = 'docs-screenshot'; button.setAttribute('aria-label', label.open + ': ' + source.alt); button.setAttribute('aria-haspopup','dialog'); button.title = label.open; button.dataset.hint = label.open;
            source.before(button); button.append(source);
            button.addEventListener('click', () => { opener = button; fit(); image.src = source.src; image.alt = source.alt; caption.textContent = source.alt || label.open; document.body.classList.add('docs-image-open'); dialog.showModal(); });
        }
        const support = document.createElement('aside'); support.className = 'docs-support';
        const text = document.createElement('p'); text.textContent = nl ? 'Heb je iets aan ProxyPermit? Met een ster op GitHub help je anderen het project te vinden.' : 'Finding ProxyPermit useful? A star on GitHub helps others discover the project.';
        const link = document.createElement('a'); link.href = 'https://github.com/Dantdmnl/ProxyPermit'; link.textContent = nl ? '☆ Geef ProxyPermit een ster op GitHub' : '☆ Star ProxyPermit on GitHub';
        support.append(text,link); article.append(support);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
