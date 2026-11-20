/* Local documentation enhancements; image viewing is handled by Zensical. */
(() => {
    function addGalleryZoom(dialog, nl) {
        const slider = dialog.querySelector('.gslider');
        const tools = document.createElement('div');
        tools.className = 'docs-gallery-tools';
        tools.setAttribute('role', 'group');
        tools.setAttribute('aria-label', nl ? 'Zoombediening' : 'Zoom controls');
        const level = document.createElement('span');
        level.setAttribute('role', 'status');
        const viewport = document.createElement('div');
        viewport.className = 'docs-gallery-viewport';
        viewport.tabIndex = 0;
        viewport.setAttribute('role', 'region');
        viewport.setAttribute('aria-label', nl ? 'Ingezoomde schermafbeelding' : 'Zoomed screenshot');
        const enlarged = document.createElement('img');
        enlarged.draggable = false;
        viewport.append(enlarged);
        const scales = [1, 1.25, 1.5, 2, 2.5, 3];
        let source, step = 0;

        function button(text, label, action) {
            const control = document.createElement('button');
            control.type = 'button';
            control.textContent = text;
            control.title = label;
            control.setAttribute('aria-label', label);
            control.addEventListener('click', action);
            return control;
        }

        const less = button('−', nl ? 'Uitzoomen' : 'Zoom out', () => setStep(step - 1));
        const more = button('+', nl ? 'Inzoomen' : 'Zoom in', () => setStep(step + 1));
        const fit = button(nl ? 'Passend' : 'Fit', nl ? 'Aan venster aanpassen' : 'Fit to window', () => setStep(0));

        function setStep(next) {
            const x = viewport.hidden ? 0.5 : (viewport.scrollLeft + viewport.clientWidth / 2) / viewport.scrollWidth;
            const y = viewport.hidden ? 0.5 : (viewport.scrollTop + viewport.clientHeight / 2) / viewport.scrollHeight;
            step = Math.max(0, Math.min(next, scales.length - 1));
            viewport.hidden = step === 0;
            slider.classList.toggle('docs-gallery-slide-obscured', step > 0);
            level.textContent = `${scales[step] * 100}%`;
            less.setAttribute('aria-disabled', String(step === 0));
            fit.setAttribute('aria-disabled', String(step === 0));
            more.setAttribute('aria-disabled', String(step === scales.length - 1));
            if (step) {
                enlarged.style.width = `${source.offsetWidth * scales[step]}px`;
                viewport.scrollLeft = x * viewport.scrollWidth - viewport.clientWidth / 2;
                viewport.scrollTop = y * viewport.scrollHeight - viewport.clientHeight / 2;
            }
        }

        function updateSlide() {
            const image = dialog.querySelector('.current .gslide-image img');
            tools.hidden = !image?.naturalWidth;
            if (image === source) return;
            if (document.activeElement === viewport || tools.hidden && tools.contains(document.activeElement)) {
                dialog.querySelector('.gclose').focus();
            }
            source = image;
            setStep(0);
            if (source) {
                enlarged.src = source.src;
                enlarged.alt = source.alt;
            }
        }

        tools.append(less, level, more, fit);
        dialog.prepend(tools, viewport);
        for (const element of [tools, viewport]) {
            element.addEventListener('click', event => event.stopPropagation());
        }
        viewport.addEventListener('keydown', event => {
            // Arrow keys scroll the enlarged image instead of changing slides.
            if (event.key.startsWith('Arrow')) event.stopPropagation();
        });
        dialog.addEventListener('wheel', event => {
            if ((!event.ctrlKey && !event.metaKey) || !source?.naturalWidth || !event.deltaY) return;
            event.preventDefault();
            setStep(step + (event.deltaY < 0 ? 1 : -1));
        }, { passive: false });
        dialog.addEventListener('click', event => {
            if (event.target === source && source.classList.contains('zoomable')) {
                event.stopPropagation();
                setStep(1);
            }
        }, true);
        dialog.addEventListener('load', updateSlide, true);
        const observer = new MutationObserver(updateSlide);
        observer.observe(slider, {
            subtree: true, childList: true, attributes: true, attributeFilter: ['class']
        });
        const resize = new ResizeObserver(() => {
            if (step) setStep(step);
        });
        resize.observe(dialog);
        setStep(0);
        updateSlide();
        return () => {
            observer.disconnect();
            resize.disconnect();
        };
    }

    function init() {
        const article = document.querySelector('article');
        if (!article || article.querySelector('.docs-support')) return;
        const nl = location.pathname.includes('/nl/');
        const enlarge = nl ? 'Vergroten' : 'Enlarge';
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
        let opener, viewer, removeZoom;
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
                removeZoom = addGalleryZoom(current, nl);
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
                    const controls = [...current.querySelectorAll('button:not(.disabled), .docs-gallery-viewport')]
                        .filter(control => control.getClientRects().length);
                    if (!controls.length) return;
                    event.preventDefault(); event.stopPropagation();
                    const index = controls.indexOf(document.activeElement);
                    controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length].focus();
                });
                current.querySelector('.gclose')?.focus();
            } else if (!current && viewer) {
                viewer = null;
                removeZoom();
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
