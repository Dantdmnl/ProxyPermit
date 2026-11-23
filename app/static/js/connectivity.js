/* Reachability is independent of authentication, proxy health and HA peer health.
 * Keep drafts in the current DOM; never cache credentials or replay mutations.
 */
(() => {
    'use strict';
    const nativeFetch = window.fetch.bind(window);
    let notice, state = 'online', pending = null, timer, uncertain = false;
    let generation = 0;
    const pageNode = () => document.querySelector('[data-management-node]')?.dataset.managementNode;

    function show() {
        // Elements outside a modal dialog are inert, even in the popover layer.
        // Keep the notice within the active dialog so retry/close remain usable.
        const dialogs = [...document.querySelectorAll('dialog[open]')];
        const parent = dialogs.at(-1) || document.body;
        if (notice.parentElement !== parent) {
            if (notice.hidePopover && notice.matches(':popover-open')) notice.hidePopover();
            parent.append(notice);
        }
        notice.dataset.state = state;
        const switched = state === 'node-changed';
        notice.querySelector('#connectivityTitle').textContent = notice.dataset[
            switched ? 'nodeChanged' : state === 'online' ? (uncertain ? 'uncertain' : 'restored') : state === 'offline' ? 'offline' : 'unavailable'];
        notice.querySelector('[data-connection-detail]').hidden = switched || state === 'online';
        notice.querySelector('[data-connection-restored]').hidden = state !== 'online';
        notice.querySelector('[data-connection-uncertain]').hidden = !uncertain;
        notice.querySelector('[data-connection-node-detail]').hidden = !switched;
        notice.querySelector('[data-connection-signin]').hidden = !switched;
        notice.querySelector('[data-connection-retry]').hidden = switched || state === 'online';
        notice.querySelector('[data-connection-dismiss]').hidden = state !== 'online';
        notice.dataset.visible = '';
        // A manual popover stays above open dialogs without moving keyboard focus.
        if (notice.showPopover && !notice.matches(':popover-open')) notice.showPopover();
    }

    function change(next) {
        if (state === 'node-changed') return;
        const previous = state;
        state = next;
        if (next !== 'online' || previous !== 'online') show();
        if (next !== previous) window.dispatchEvent(new CustomEvent('proxypermit:connection', { detail: { state: next } }));
    }

    function schedule() {
        clearTimeout(timer);
        if (state !== 'node-changed') timer = setTimeout(check, state === 'online' ? 15000 : 5000);
    }

    async function check() {
        if (!notice || state === 'node-changed') return;
        if (!navigator.onLine) { change('offline'); schedule(); return; }
        if (pending) return pending;
        const current = generation;
        const retry = notice.querySelector('[data-connection-retry]');
        retry.disabled = true;
        retry.textContent = notice.dataset.checking;
        pending = (async () => {
            try {
                const response = await nativeFetch('/connection', {
                    cache: 'no-store', credentials: 'omit', redirect: 'error',
                    signal: AbortSignal.timeout(4000)
                });
                if (generation === current) {
                    const reachable = response.status === 204 && response.headers.get('X-ProxyPermit-Reachable') === '1';
                    const role = response.headers.get('X-ProxyPermit-Node');
                    const switched = reachable && pageNode() && role && role !== pageNode();
                    change(switched ? 'node-changed' : reachable ? 'online' : 'unavailable');
                }
            } catch {
                if (generation === current) change(navigator.onLine ? 'unavailable' : 'offline');
            } finally {
                pending = null;
                retry.disabled = false;
                retry.textContent = notice.dataset.retry;
                schedule();
            }
        })();
        return pending;
    }

    // Observe same-origin failures, then confirm them with a cheap independent probe.
    // HTTP validation/authentication errors and deliberately cancelled requests are not outages.
    window.fetch = async (input, options) => {
        const url = new URL(input instanceof Request ? input.url : input, location.href);
        const local = url.origin === location.origin;
        const method = (options?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const unsafeMethod = !['GET', 'HEAD', 'OPTIONS'].includes(method);
        const validation = method === 'POST' && ['/api/validate', '/api/proxy/config/validate',
            '/api/settings/ha/preview', '/api/settings/cluster-plan/preview', '/api/merge/remote-preview',
            '/api/proxy/ha-replication/status', '/api/proxy/ha-policy/status'].includes(url.pathname);
        const mutation = unsafeMethod && !validation;
        if (local && unsafeMethod && state !== 'online') {
            show();
            void check();
            throw new TypeError(notice.dataset[state === 'node-changed' ? 'nodeChanged' : state === 'offline' ? 'offline' : 'unavailable']);
        }
        if (local && url.pathname.startsWith('/api/') && pageNode()) {
            const headers = new Headers(options?.headers ?? (input instanceof Request ? input.headers : undefined));
            headers.set('X-ProxyPermit-Page-Node', pageNode());
            options = { ...options, headers };
        }
        try {
            const response = await nativeFetch(input, options);
            if (local && response.status === 409 && response.headers.get('X-ProxyPermit-Node-Changed') === '1' && notice) {
                change('node-changed');
                schedule();
            }
            if (local && response.status >= 500) {
                if (mutation) { uncertain = true; if (notice) show(); }
                void check();
            }
            return response;
        } catch (error) {
            if (local && (mutation || error.name !== 'AbortError')) {
                if (mutation) { uncertain = true; if (notice) show(); }
                void check();
            }
            throw error;
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        notice = document.getElementById('connectivityNotice');
        if (!notice) return;
        window.addEventListener('proxypermit:https-transition', event => {
            try {
                const url = new URL(event.detail.url);
                const currentPort = location.port || (location.protocol === 'http:' ? '80' : '443');
                if (url.protocol !== 'https:' || url.hostname !== location.hostname
                        || (url.port || '443') !== currentPort || url.pathname !== '/settings'
                        || url.username || url.password || url.search || url.hash) return;
                const link = notice.querySelector('[data-https-transition]');
                link.href = url.href;
                link.hidden = false;
            } catch { /* Only a same-host, same-port HTTPS upgrade is accepted. */ }
        });
        notice.querySelector('[data-connection-retry]').addEventListener('click', check);
        notice.querySelector('[data-connection-signin]').addEventListener('click', () => {
            const next = new URL(location.href);
            next.searchParams.set('node_changed', '1');
            location.replace(next);
        });
        new MutationObserver(() => {
            if ('visible' in notice.dataset) show();
        }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open'] });
        window.addEventListener('proxypermit:locale', () => { if ('visible' in notice.dataset) show(); });
        notice.querySelector('[data-connection-dismiss]').addEventListener('click', () => {
            if (notice.hidePopover && notice.matches(':popover-open')) notice.hidePopover();
            delete notice.dataset.visible;
            uncertain = false;
        });
        window.addEventListener('offline', () => { generation++; change('offline'); schedule(); });
        window.addEventListener('online', () => { generation++; void check(); });
        document.addEventListener('visibilitychange', () => { if (!document.hidden) void check(); });
        window.addEventListener('pageshow', () => { void check(); });
        document.addEventListener('click', event => {
            const link = event.target.closest('a[href]');
            if (!link || state === 'online' || link.hasAttribute('download')) return;
            const url = new URL(link.href, location.href);
            if (url.origin === location.origin && (url.pathname !== location.pathname || url.search !== location.search)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                show();
                void check();
            }
        }, true);
        document.addEventListener('submit', event => {
            if (state !== 'online') { event.preventDefault(); event.stopImmediatePropagation(); show(); void check(); }
        }, true);
        void check();
    });
})();
