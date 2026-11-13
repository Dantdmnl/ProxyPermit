// ===== GLOBAL STATE & CONFIG =====
let appConfigEl = document.getElementById('appConfig');
let appConfig = appConfigEl ? JSON.parse(appConfigEl.textContent || '{}') : {};
let vipAddress = appConfig.vipAddress || '';
let nodeAddresses = Array.isArray(appConfig.nodeAddresses) ? appConfig.nodeAddresses.filter(Boolean) : [];
// Mapping van IP naar hostname voor https, bijv: {"10.0.0.1": "node1.example.com", ...}
let peerHostnames = appConfig.peerHostnames || {};
        /**
         * Resolveer het juiste adres voor een peer, afhankelijk van het protocol.
         * Bij http: gebruik IP. Bij https: gebruik hostname uit mapping, anders waarschuwing.
         */
        function getPeerAddress(ip, scheme = 'http') {
            if (scheme === 'https') {
                if (peerHostnames[ip]) {
                    return peerHostnames[ip];
                } else {
                    // Toon waarschuwing in UI/log
                    console.warn(`Geen hostname mapping voor peer ${ip} bij https. Gebruik een geldige hostname in appConfig.peerHostnames!`);
                    // Fallback: gebruik IP, maar browser zal dit blokkeren bij invalid cert
                    return ip;
                }
            }
            return ip;
        }
let vipHostFallback = vipAddress || window.location.hostname || '';
let vipUrl = appConfig.vipUrl || (vipHostFallback ? `http://${vipHostFallback}:8080` : window.location.origin);
let nodeUrls = Array.isArray(appConfig.nodeUrls) ? appConfig.nodeUrls.filter(Boolean) : [];
let authEnabled = !!appConfig.authEnabled;
let demoMode = !!appConfig.demoMode;

function updateDemoBanner() {
    const demoBanner = document.getElementById('demoBanner');
    if (!demoBanner) return;
    demoBanner.style.display = demoMode ? 'inline-flex' : 'none';
}

updateDemoBanner();

// Update client-side state from a newly fetched config
function applyAppConfig(newCfg) {
    try {
        appConfig = Object.assign({}, appConfig, newCfg || {});
        vipAddress = appConfig.vipAddress || '';
        nodeAddresses = Array.isArray(appConfig.nodeAddresses) ? appConfig.nodeAddresses.filter(Boolean) : [];
        peerHostnames = appConfig.peerHostnames || {};
        vipHostFallback = vipAddress || window.location.hostname || '';
        vipUrl = appConfig.vipUrl || (vipHostFallback ? `http://${vipHostFallback}:8080` : window.location.origin);
        nodeUrls = Array.isArray(appConfig.nodeUrls) ? appConfig.nodeUrls.filter(Boolean) : [];
        authEnabled = !!appConfig.authEnabled;
        demoMode = !!appConfig.demoMode;
        updateDemoBanner();
    } catch (e) {
        console.warn('applyAppConfig failed', e);
    }
}

// Fetch full config from server (authenticated) and apply it
async function fetchFullConfig() {
    try {
        const resp = await fetchWithTimeout('/api/config', { method: 'GET' }, 6);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await parseJsonSafe(resp);
        if (!data || !data.success) throw new Error((data && data.error) || I18n.t('Geen config'));
        applyAppConfig(data.config || {});
        // Replace server-injected element for debugging/consistency
        if (appConfigEl) appConfigEl.textContent = JSON.stringify(appConfig);
    } catch (err) {
        console.warn('fetchFullConfig failed', err.message || err);
        throw err;
    }
}

function getHostFromUrl(url) {
    try {
        return new URL(url).hostname;
    } catch (error) {
        return '';
    }
}

function isDirectHost(hostname) {
    return nodeAddresses.includes(hostname);
}

function isDirectConnectionUrl(baseUrl) {
    return isDirectHost(getHostFromUrl(baseUrl));
}

function getConnectionLabel(mode, includeAddress = true) {
    const baseLabels = {
        vip: '📡 VIP',
        node0: '🖥️ Node 0',
        node1: '🖥️ Node 1'
    };
    const addressMap = {
        vip: vipAddress,
        node0: nodeAddresses[0],
        node1: nodeAddresses[1]
    };
    const label = baseLabels[mode] || I18n.t('Onbekend');
    const address = addressMap[mode];
    if (includeAddress && address) {
        return `${label} (${address})`;
    }
    return label;
}

function updateConnectionOptions() {
    const vipOption = document.querySelector('#connectionMode option[value="vip"]');
    const node0Option = document.querySelector('#connectionMode option[value="node0"]');
    const node1Option = document.querySelector('#connectionMode option[value="node1"]');

    if (vipOption) vipOption.textContent = `${getConnectionLabel('vip')} (Automatische failover)`;
    if (node0Option) node0Option.textContent = getConnectionLabel('node0');
    if (node1Option) node1Option.textContent = getConnectionLabel('node1');
}

let currentBaseUrl = window.location.origin;
let isDirectNode = isDirectHost(window.location.hostname);

let entries = [];
let filteredEntries = [];
let currentEditId = null;
let currentEditRevision = null;
let currentEditType = null;
let entriesRevision = null;
let statusPending = false;
let statusExpiry;
let nodeStatusEpoch = 0;
let lastStatusAt = 0;
function rulesWritable() {
    const writable = appConfig.canEditRules !== false;
    const notice = document.getElementById('rulesAccessNotice');
    if (notice) {
        notice.hidden = writable;
        notice.textContent = I18n.t(appConfig.canManageProxy === false ? 'Je monitorrol geeft alleen leesrechten. Je kunt status, regels en logs bekijken.' : 'Deze backup ontvangt gedeeld beleid van node A. Voeg regels toe, wijzig, verwijder of verplaats ze op node A; ook wanneer deze node het VIP bedient.');
    }
    return writable;
}
function requireRulesWritable() {
    if (rulesWritable()) return true;
    document.getElementById('rulesAccessNotice')?.scrollIntoView({ block: 'nearest' });
    showToast('warning', I18n.t('Alleen lezen'), I18n.t(appConfig.canManageProxy === false ? 'Je rol staat deze actie niet toe.' : 'Publish shared proxy policy on the primary HA node.'));
    return false;
}
function markNodeStatusUnknown() {
    if (appConfig.deploymentMode !== 'standalone') {
        document.getElementById('vipStatusText').textContent = I18n.t('VIP-status niet bevestigd');
        document.getElementById('vipStatusDot').className = 'status-dot unknown';
    }
    document.getElementById('tinyproxyStatusText').textContent = I18n.t('Servicestatus niet bevestigd');
    document.getElementById('tinyproxyStatusDot').className = 'status-dot unknown';
    document.getElementById('nodeRoleBadge').className = 'node-badge unknown';
    document.getElementById('nodeRoleBadge').textContent = I18n.t('Status niet bevestigd');
}
window.addEventListener('proxypermit:connection', event => {
    if (event.detail.state !== 'online') { nodeStatusEpoch++; markNodeStatusUnknown(); }
    else { refreshStatus(); loadEntriesDebounced(0); }
});
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastStatusAt > 12000) { markNodeStatusUnknown(); refreshStatus(); }
});
let draggedEntryId = null;
let isSavingOrder = false;
let autoScrollInterval = null;
let searchQuery = '';
let loadEntriesTimer = null;
let isLoadingEntries = false;
let consecutiveFailures = 0;
let manualSyncPollTimer = null;
let isAuthenticated = !authEnabled;
let lastWhitelistMtime;

// Init
document.addEventListener('DOMContentLoaded', () => {
    // Log connection mode
    console.log('[Init] Base URL:', currentBaseUrl);
    console.log('[Init] Window location:', window.location.origin);

    // Load settings and credentials
    updateConnectionOptions();
    initAuthStatus();

    // Restore connection mode
    updateConnectionStatus();

    // Auto-refresh status elke 30 seconden
    const statusRefreshSeconds = parseInt(localStorage.getItem('setting_statusRefresh') || '30');
    const statusRefreshInterval = Math.max(5, statusRefreshSeconds) * 1000;
    setInterval(refreshStatus, statusRefreshInterval);
    if (statusRefreshSeconds > 10) {
        setInterval(() => {
            if (document.visibilityState === 'visible') {
                refreshStatus();
            }
        }, 8000);
    }

});

async function startAuthenticatedSession() {
    try {
        await fetchFullConfig();
    } catch (e) {
        // If fetching full config fails, proceed but some features may remain limited
        console.warn('Could not fetch full config after login:', e);
    }
    loadEntries();
    refreshStatus();
    initManualSyncStatus();
}

async function getAuthTransferToken() {
    if (!authEnabled || !isAuthenticated) return null;

    try {
        const result = await apiRequest('/api/auth/transfer', {
            method: 'POST'
        }, { timeoutSeconds: 6, retries: 0, suppressAuthPrompt: true });

        if (result.ok && result.data && result.data.success && result.data.token) {
            return result.data.token;
        }
    } catch (error) {
        return null;
    }
    return null;
}

function formatRelativeTime(isoString) {
    if (!isoString) return null;
    const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(isoString);
    const normalized = hasTimezone ? isoString : `${isoString}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return null;

    const diffMs = Date.now() - date.getTime();
    const diffMin = Math.max(0, Math.floor(diffMs / 60000));
    if (diffMin < 60) {
        return { label: `${diffMin}m`, stale: false };
    }

    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) {
        return { label: `${diffHours}h`, stale: true };
    }

    const diffDays = Math.floor(diffHours / 24);
    return { label: `${diffDays}d`, stale: true };
}


function setManualSyncButtonState(isRunning) {
    const button = document.getElementById('manualSyncButton');
    const spinner = document.getElementById('manualSyncSpinner');
    const label = document.getElementById('manualSyncLabel');

    if (!button || !spinner || !label) return;

    button.disabled = isRunning;
    spinner.style.display = isRunning ? 'inline-block' : 'none';
    label.textContent = isRunning ? I18n.t('Sync bezig...') : I18n.t('🔄 Handmatige sync');
}

async function initManualSyncStatus() {
    if (appConfig.deploymentMode === 'standalone' || appConfig.managedHa) return;
    try {
        const result = await apiRequest('/api/sync/run/status', {}, { timeoutSeconds: 3, retries: 0 });
        const state = result.data && result.data.state ? result.data.state : {};
        if (state.running) {
            setManualSyncButtonState(true);
            startManualSyncPolling();
        }
    } catch (error) {
        // Silently ignore; sync status is optional
    }
}

function startManualSyncPolling() {
    if (manualSyncPollTimer) {
        clearInterval(manualSyncPollTimer);
    }
    pollManualSyncStatus();
    manualSyncPollTimer = setInterval(pollManualSyncStatus, 10000);
}

async function pollManualSyncStatus() {
    try {
        const result = await apiRequest('/api/sync/run/status', {}, { timeoutSeconds: 3, retries: 0 });
        const state = result.data && result.data.state ? result.data.state : {};

        if (state.running) {
            setManualSyncButtonState(true);
            return;
        }

        if (manualSyncPollTimer) {
            clearInterval(manualSyncPollTimer);
            manualSyncPollTimer = null;
        }

        setManualSyncButtonState(false);

        if (state.last_success === true) {
            showToast('success', I18n.t('Sync voltooid'), 'Synchronisatie afgerond');
            loadEntriesDebounced(300);
        } else if (state.last_success === false) {
            showToast('error', I18n.t('Sync fout'), I18n.t('Synchronisatie mislukt'));
        }

        refreshStatus();
        setTimeout(checkSyncConflicts, 1200);
    } catch (error) {
        if (manualSyncPollTimer) {
            clearInterval(manualSyncPollTimer);
            manualSyncPollTimer = null;
        }
        setManualSyncButtonState(false);
    }
}

async function triggerManualSync() {
    setManualSyncButtonState(true);
    try {
        const result = await apiRequest('/api/sync/run', {
            method: 'POST'
        }, { timeoutSeconds: 8, retries: 0 });

        showToast('info', 'Sync gestart', 'Handmatige synchronisatie gestart');
        startManualSyncPolling();
    } catch (error) {
        const message = (error && error.message) ? error.message : I18n.t('Synchronisatie mislukt');
        if (message.toLowerCase().includes('draait')) {
            showToast('info', I18n.t('Sync bezig'), message);
            startManualSyncPolling();
            return;
        }
        setManualSyncButtonState(false);
        showToast('error', I18n.t('Sync fout'), message);
    }
}

// Check for sync conflicts
async function checkSyncConflicts() {
    if (appConfig.deploymentMode === 'standalone' || appConfig.managedHa) return;
    try {
        const result = await apiRequest('/api/sync/status', {}, { timeoutSeconds: 3, retries: 0 });

        let state = result.ok && result.data ? result.data.state : null;
        let peerState = null;
        let peerLabel = I18n.t('onbekend');
        let isConflict = false;

        if (state) {
            const peerResult = await apiRequest('/api/sync/peer-status', {}, { timeoutSeconds: 4, retries: 0 });
            peerState = peerResult.ok && peerResult.data ? peerResult.data.peer_status : null;
            isConflict = Boolean(state.conflict || (peerState && peerState.conflict));
            peerLabel = state.peer || peerLabel;
        } else {
            // No local conflict, but return (nothing to show)
            return;
        }

        // Check for conflict
        if (isConflict) {
            // Alleen waarschuwing tonen, geen automatische merge-modal
            const conflictWarning = document.getElementById('syncConflictWarning');
            if (!conflictWarning) {
                const notice = document.createElement('div');
                notice.id = 'syncConflictWarning';
                notice.style.cssText = `
                    background: #f8d7da;
                    color: #721c24;
                    border: 1px solid #f5c6cb;
                    border-radius: 4px;
                    padding: 10px 15px;
                    margin-top: 10px;
                    display: flex;
                    gap: 8px;
                    align-items: center;
                `;
                notice.innerHTML = I18n.html`
                    <span style="font-weight: bold;">🔄 Synchronisatieconflict:</span>
                    <span>Beide nodes hebben recente whitelist-wijzigingen. Handmatige controle vereist om dataverlies te voorkomen. Peer: ${peerLabel}</span>
                    <button class="button button-secondary" style="margin-left: auto;" onclick="openMergeModal()">Merge oplossen</button>
                `;
                document.querySelector('.status-bar').appendChild(notice);
            }
        } else {
            const warning = document.getElementById('syncConflictWarning');
            if (warning) {
                warning.remove();
            }
        }

    } catch (error) {
        // Silently fail - sync status is optional
        console.debug('Sync status check failed:', error);
    }
}

// Merge modal
let _mergeModalEscHandler = null;
function openMergeModal() {
    if (!requireRulesWritable()) return;
    const modal = document.getElementById('mergeModal');
    if (!modal) return;
    modal.classList.add('show');
    initialMergeItems = '';
    loadMergeData();
    document.body.classList.add('modal-open');
    if (_mergeModalEscHandler) document.removeEventListener('keydown', _mergeModalEscHandler);
    _mergeModalEscHandler = function(e) { if (e.key === 'Escape') closeMergeModal(); };
    document.addEventListener('keydown', _mergeModalEscHandler);
}

let initialMergeItems = '';

function closeMergeModal(saved = false) {
    const modal = document.getElementById('mergeModal');
    if (!modal) return;
    if (!saved && !confirmDiscard(initialMergeItems && JSON.stringify(window.mergeItems) !== initialMergeItems)) return;
    modal.classList.remove('show');
    document.body.classList.remove('modal-open');
    if (_mergeModalEscHandler) {
        document.removeEventListener('keydown', _mergeModalEscHandler);
        _mergeModalEscHandler = null;
    }
}

function parseWhitelistLines(content) {
    if (!content) return [];
    return content
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => line.length > 0);
}

// Parse a whitelist line into {content, description, raw, line, hasComment}
function parseWhitelistLine(line) {
    // Return a structured representation of a whitelist line.
    // Handles three cases:
    //  - comment-only lines starting with optional whitespace + '#'
    //  - content lines with an inline comment (separated by two+ spaces or a tab before '#')
    //  - plain content lines
    const raw = (line || '');
    const trimmed = raw.trim();
    let content = null;
    let description = null;
    let hasComment = false;
    let isComment = false;

    // Comment-only lines: optional leading whitespace then '#'
    const commentOnly = raw.match(/^\s*#\s*(.*)$/);
    if (commentOnly) {
        isComment = true;
        hasComment = true;
        description = (commentOnly[1] || '').trim();
        // leave content as null to identify comment entries
        return { content: null, description, raw, line: raw, hasComment, isComment };
    }

    // Inline comment when separated by one or more whitespace before '#'
    // Example: "^test\\.com$ # comment" should parse as content + description.
    const inline = raw.match(/^(.*?)(?:\s+)#\s*(.*)$/);
    if (inline) {
        content = (inline[1] || '').trim();
        description = (inline[2] !== undefined) ? (inline[2] || '') : null;
        hasComment = true;
        return { content, description, raw, line: raw, hasComment, isComment };
    }

    // Plain content (no comment)
    content = trimmed.length ? trimmed : null;
    return { content, description: null, raw, line: raw, hasComment: false, isComment: false };
}

// Merge logic: deduplicate by content, pick newest comment (simulate with order)
function buildMergeItems(localLines, remoteLines) {
    // Build merge items while handling comment-only lines separately and normalizing keys.
    // Use a composite key: comments are keyed by their raw line (preserve order),
    // normal entries are keyed by normalized content (lowercased).
    const map = new Map();
    function normalizeKey(entry) {
        if (entry.isComment) return 'COMMENT::' + entry.raw;
        return 'CONTENT::' + (entry.content || '').toLowerCase();
    }

    function addOrUpdate(entry, source) {
        const key = normalizeKey(entry);
        let existing = map.get(key);
        if (!existing) {
            map.set(key, {
                // keep original content (not normalized) for display
                content: entry.content,
                description: entry.description,
                raw: entry.raw,
                isComment: !!entry.isComment,
                inLocal: source === 'local',
                inRemote: source === 'remote',
                localDescription: source === 'local' ? entry.description : null,
                remoteDescription: source === 'remote' ? entry.description : null,
                selected: true
            });
        } else {
            if (source === 'local') {
                existing.inLocal = true;
                existing.localDescription = entry.description;
                if (!existing.raw && entry.raw) existing.raw = entry.raw;
                if (!existing.content && entry.content) existing.content = entry.content;
            } else {
                existing.inRemote = true;
                existing.remoteDescription = entry.description;
                if (!existing.raw && entry.raw) existing.raw = entry.raw;
                if (!existing.content && entry.content) existing.content = entry.content;
            }
        }
    }

    localLines.forEach(line => addOrUpdate(parseWhitelistLine(line), 'local'));
    remoteLines.forEach(line => addOrUpdate(parseWhitelistLine(line), 'remote'));

    const items = [];
    for (const [key, entry] of map.entries()) {
        let description = null;
        // If comment-only entry, prefer descriptions as-is and preserve raw text
        if (entry.isComment) {
            description = entry.description || '';
        } else {
            const localDesc = entry.localDescription;
            const remoteDesc = entry.remoteDescription;
            // Explicit empty string ('') means the comment was removed on that side.
            // If either side explicitly removed the description (''), treat as deletion.
            if (localDesc === '' || remoteDesc === '') {
                description = '';
            } else if (remoteDesc != null && remoteDesc !== '') {
                description = remoteDesc;
            } else if (localDesc != null && localDesc !== '') {
                description = localDesc;
            } else {
                description = null;
            }
        }

        // Rebuild line for preview; for comments prefer raw formatting
        let lineText = '';
        if (entry.isComment) {
            // Keep the original raw comment when possible, otherwise reconstruct
            lineText = entry.raw && entry.raw.trim() ? entry.raw.trim() : ('# ' + (description || ''));
        } else if (entry.content) {
            lineText = entry.content;
            if (description != null && description.length > 0) {
                lineText += '  # ' + description;
            }
        } else {
            lineText = entry.raw || '';
        }

        items.push({
            line: lineText,
            content: entry.content,
            description,
            inLocal: !!entry.inLocal,
            inRemote: !!entry.inRemote,
            localDescription: entry.localDescription != null ? entry.localDescription : '',
            remoteDescription: entry.remoteDescription != null ? entry.remoteDescription : '',
            isComment: !!entry.isComment,
            raw: entry.raw || '',
            selected: true,
            // chosenDescription can be changed by the user in the UI (defaults to merged decision)
            chosenDescription: (description === undefined) ? null : description
        });
    }
    return items;
}

function updateMergeSummary(items) {
    const summaryEl = document.getElementById('mergeSummary');
    if (!summaryEl) return;

    const counts = {
        localOnly: items.filter(item => item.inLocal && !item.inRemote).length,
        remoteOnly: items.filter(item => item.inRemote && !item.inLocal).length,
        shared: items.filter(item => item.inLocal && item.inRemote).length
    };
    summaryEl.textContent = I18n.msg`Alleen lokaal: ${counts.localOnly} • Alleen remote: ${counts.remoteOnly} • Beide: ${counts.shared}`;
}

function updateMergeToolbarCounts(items) {
    const counts = {
        all: items.length,
        local: items.filter(i => i.inLocal).length,
        remote: items.filter(i => i.inRemote).length,
        shared: items.filter(i => i.inLocal && i.inRemote).length
    };
    const elAll = document.getElementById('countAll');
    const elLocal = document.getElementById('countLocal');
    const elRemote = document.getElementById('countRemote');
    const elShared = document.getElementById('countShared');
    if (elAll) elAll.textContent = counts.all;
    if (elLocal) elLocal.textContent = counts.local;
    if (elRemote) elRemote.textContent = counts.remote;
    if (elShared) elShared.textContent = counts.shared;
}

function setMergePreviewMode(mode) {
    window.mergePreviewMode = mode;
    updateMergePreview(window.mergeItems || []);
}

function buildMergePreviewLines(localLines, mergedLines) {
    const localSet = new Set((localLines || []).map(l => (l || '').trim()));
    const mergedSet = new Set((mergedLines || []).map(l => (l || '').trim()));
    const lines = [];

    (localLines || []).forEach(line => {
        const t = (line || '').trim();
        if (!mergedSet.has(t)) {
            lines.push({ type: 'del', line });
        }
    });

    (mergedLines || []).forEach(line => {
        const t = (line || '').trim();
        if (!localSet.has(t)) {
            lines.push({ type: 'add', line });
        }
    });

    return lines;
}


// Scroll-synchronisatie tussen lijst en preview (exacte verhouding)
function syncMergeScroll() {
    const listEl = document.getElementById('mergeList');
    const previewEl = document.getElementById('mergePreview');
    if (!listEl || !previewEl) return;
    let isSyncing = false;
    function sync(from, to) {
        if (isSyncing) return;
        isSyncing = true;
        const ratio = from.scrollTop / (from.scrollHeight - from.clientHeight);
        to.scrollTop = ratio * (to.scrollHeight - to.clientHeight);
        isSyncing = false;
    }
    listEl.addEventListener('scroll', function() { sync(listEl, previewEl); });
    previewEl.addEventListener('scroll', function() { sync(previewEl, listEl); });
}
// Init scroll sync na laden lijst
setTimeout(syncMergeScroll, 500);

// Custom confirm modal that returns a Promise<boolean>
function showConfirmModal(messageHtml) {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirmModal');
        const msg = document.getElementById('confirmMessage');
        const ok = document.getElementById('confirmOk');
        const cancel = document.getElementById('confirmCancel');
        if (!modal || !msg || !ok || !cancel) {
            // Fallback to native confirm if modal elements missing (escape to plain text)
            const plain = (typeof messageHtml === 'string') ? messageHtml.replace(/<[^>]*>/g, '') : String(messageHtml);
            resolve(window.confirm(plain));
            return;
        }
        // messageHtml is expected to be safe HTML (we control the content)
        msg.innerHTML = messageHtml;
        modal.classList.add('show');
        document.body.classList.add('modal-open');
        // Accessibility & resilience: support ESC to cancel and click-outside to cancel
        function cleanup(result) {
            modal.classList.remove('show');
            document.body.classList.remove('modal-open');
            ok.removeEventListener('click', onOk);
            cancel.removeEventListener('click', onCancel);
            modal.removeEventListener('click', onBackdropClick);
            document.removeEventListener('keydown', onKeyDown);
            resolve(result);
        }
        function onOk() { cleanup(true); }
        function onCancel() { cleanup(false); }
        function onBackdropClick(e) {
            // if clicking outside the content area, treat as cancel
            if (e.target === modal) cleanup(false);
        }
        function onKeyDown(e) {
            if (e.key === 'Escape') cleanup(false);
            if (e.key === 'Enter') {
                // Enter should confirm only when focus not on cancel
                const active = document.activeElement;
                if (active !== cancel) cleanup(true);
            }
        }
        ok.addEventListener('click', onOk);
        cancel.addEventListener('click', onCancel);
        modal.addEventListener('click', onBackdropClick);
        document.addEventListener('keydown', onKeyDown);
        // Focus the OK button for keyboard users
        try { ok.focus(); } catch (e) {/* ignore */}
    });
}

function setMergeSelection(mode) {
    if (!window.mergeItems) return;
    window.mergeItems.forEach(item => {
        if (mode === 'all') {
            item.selected = true;
        } else if (mode === 'none') {
            item.selected = false;
        } else if (mode === 'local') {
            // Select any item that exists locally (including shared)
            item.selected = !!item.inLocal;
            // For shared non-comment items, prefer local description/source explicitly.
            if (item.inLocal && item.inRemote && !item.isComment) {
                // Preserve explicit local empty description ('') as a valid choice.
                const chosen = (item.localDescription != null) ? item.localDescription : '';
                item.chosenDescription = chosen;
                if (item.content) {
                    item.line = item.content + (chosen != null && chosen.length > 0 ? ('  # ' + chosen) : '');
                }
            }
        } else if (mode === 'remote') {
            // Select any item that exists on the remote (including shared)
            item.selected = !!item.inRemote;
            // For shared non-comment items, prefer remote description/source explicitly.
            if (item.inLocal && item.inRemote && !item.isComment) {
                // Preserve explicit remote empty description ('') as a valid choice.
                const chosen = (item.remoteDescription != null) ? item.remoteDescription : '';
                item.chosenDescription = chosen;
                if (item.content) {
                    item.line = item.content + (chosen != null && chosen.length > 0 ? ('  # ' + chosen) : '');
                }
            }
        } else if (mode === 'shared') {
            item.selected = item.inLocal && item.inRemote;
        }
    });
    renderMergeList(window.mergeItems);
    updateMergeToolbarActive(mode);
}

function updateMergeToolbarActive(mode) {
    const ids = ['btnSelectAll','btnSelectNone','btnSelectLocal','btnSelectRemote','btnSelectShared'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('active');
    });
    if (!mode) return;
    const map = {
        all: 'btnSelectAll',
        none: 'btnSelectNone',
        local: 'btnSelectLocal',
        remote: 'btnSelectRemote',
        shared: 'btnSelectShared'
    };
    const target = document.getElementById(map[mode]);
    if (target) target.classList.add('active');
}

function setMergeSelectionForSection(sectionKey, selected) {
    if (!window.mergeItems) return;
    window.mergeItems.forEach(item => {
        const isLocalOnly = item.inLocal && !item.inRemote;
        const isRemoteOnly = item.inRemote && !item.inLocal;
        const isShared = item.inLocal && item.inRemote;

        if (sectionKey === 'local' && isLocalOnly) item.selected = selected;
        if (sectionKey === 'remote' && isRemoteOnly) item.selected = selected;
        if (sectionKey === 'shared' && isShared) item.selected = selected;
    });
    renderMergeList(window.mergeItems);
}

function renderMergeList(items) {
    const listEl = document.getElementById('mergeList');
    if (!listEl) return;
    listEl.innerHTML = '';

    if (!items.length) {
        listEl.innerHTML = I18n.fragment('<div class="domain-hint">Geen whitelist regels gevonden.</div>');
        return;
    }

    const sections = [
        {
            title: 'Alleen lokaal',
            filter: item => item.inLocal && !item.inRemote
        },
        {
            title: 'Alleen remote',
            filter: item => item.inRemote && !item.inLocal
        },
        {
            title: 'Beide',
            filter: item => item.inLocal && item.inRemote
        }
    ];

    sections.forEach(section => {
        const sectionItems = items.filter(section.filter);
        if (!sectionItems.length) return;
        const title = document.createElement('div');
        title.className = 'merge-section-title';
        title.textContent = section.title;
        listEl.appendChild(title);

        sectionItems.forEach(item => {
            const index = items.indexOf(item);
            const row = document.createElement('div');
            const rowClass = item.inLocal && item.inRemote
                ? 'merge-item merge-shared'
                : (item.inLocal ? 'merge-item merge-local' : 'merge-item merge-remote');
            row.className = rowClass;

            const label = document.createElement('label');
            label.style.display = 'flex';
            label.style.alignItems = 'center';
            label.style.gap = '10px';
            label.style.flex = '1';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = item.selected;
            checkbox.dataset.index = String(index);

            const code = document.createElement('code');
            code.textContent = item.line;

            label.appendChild(checkbox);
            label.appendChild(code);

            // Description chooser: compact segmented control (auto / local / peer / none)
            if (!item.isComment) {
                const chooser = document.createElement('div');
                chooser.className = 'desc-chooser';
                chooser.dataset.index = String(index);

                function makeBtn(key, title, label) {
                    const b = document.createElement('button');
                    b.type = 'button';
                    b.className = 'desc-btn';
                    b.title = title;
                    b.setAttribute('aria-label', title);
                    b.dataset.key = key;
                    // Use simple emoji icons for consistent rendering across environments
                    let emoji = '';
                    if (key === 'auto') emoji = '⚙️';
                    else if (key === 'local') emoji = '🏠';
                    else if (key === 'remote') emoji = '☁️';
                    else if (key === 'none') emoji = '🗑️';
                    b.innerHTML = I18n.fragment('<span class="icon">') + emoji + I18n.fragment('</span>') + I18n.fragment('<span class="sr-only">') + (label || title) + I18n.fragment('</span>');
                    return b;
                }

                const btnAuto = makeBtn('auto', I18n.t('Standaard (voorkeur: peer → lokaal)'), I18n.t('Standaard'));
                const btnLocal = makeBtn('local', I18n.t('Behoud lokaal (houd lokale beschrijving)'), I18n.t('Lokaal'));
                const btnRemote = makeBtn('remote', 'Behoud peer (houd peer beschrijving)', 'Peer');
                const btnNone = makeBtn('none', I18n.t('Geen beschrijving'), I18n.t('Verwijder'));

                // Disable buttons where the option isn't meaningful: e.g. no local description available
                // Disable local/remote when there's no description on that side
                // or when that description is identical to the other side (no-op).
                if (!(item.localDescription != null)) {
                    btnLocal.disabled = true;
                    btnLocal.title = I18n.t('Geen lokale beschrijving beschikbaar');
                    btnLocal.setAttribute('aria-disabled', 'true');
                } else if (item.remoteDescription != null && item.localDescription === item.remoteDescription) {
                    btnLocal.disabled = true;
                    btnLocal.title = I18n.t('Lokaal en peer hebben dezelfde beschrijving (geen verschil)');
                    btnLocal.setAttribute('aria-disabled', 'true');
                }
                if (!(item.remoteDescription != null)) {
                    btnRemote.disabled = true;
                    btnRemote.title = I18n.t('Geen peer-beschrijving beschikbaar');
                    btnRemote.setAttribute('aria-disabled', 'true');
                } else if (item.localDescription != null && item.remoteDescription === item.localDescription) {
                    btnRemote.disabled = true;
                    btnRemote.title = I18n.t('Peer en lokaal hebben dezelfde beschrijving (geen verschil)');
                    btnRemote.setAttribute('aria-disabled', 'true');
                }

                // Disable 'none' when there is nothing to remove (no description on either side)
                const hasAnyDescription = (item.localDescription != null && item.localDescription !== '') || (item.remoteDescription != null && item.remoteDescription !== '');
                if (!hasAnyDescription) {
                    btnNone.disabled = true;
                    btnNone.title = I18n.t('Geen beschrijving aanwezig om te verwijderen');
                    btnNone.setAttribute('aria-disabled', 'true');
                }

                chooser.appendChild(btnAuto);
                chooser.appendChild(btnLocal);
                chooser.appendChild(btnRemote);
                chooser.appendChild(btnNone);

                const preview = document.createElement('div');
                preview.className = 'desc-preview';
                preview.textContent = (item.chosenDescription !== undefined && item.chosenDescription !== null) ? (item.chosenDescription || I18n.t('(geen)')) : (item.localDescription || item.remoteDescription || I18n.t('(geen)'));
                chooser.appendChild(preview);

                // set active based on current chosenDescription
                const cur = (item.chosenDescription !== undefined && item.chosenDescription !== null) ? item.chosenDescription : item.description;
                function setActiveByValue(val) {
                    [btnAuto, btnLocal, btnRemote, btnNone].forEach(b => b.classList.remove('active'));
                    if (val === null || val === undefined) { btnAuto.classList.add('active'); }
                    else if (val === '') { btnNone.classList.add('active'); }
                    else if (item.remoteDescription != null && val === item.remoteDescription) { btnRemote.classList.add('active'); }
                    else if (item.localDescription != null && val === item.localDescription) { btnLocal.classList.add('active'); }
                    else { btnAuto.classList.add('active'); }
                }
                setActiveByValue(cur);

                function applyChoice(choiceKey) {
                    const it = window.mergeItems[Number(chooser.dataset.index)];
                    if (!it) return;
                    let chosen = null;
                    if (choiceKey === 'none') chosen = '';
                    else if (choiceKey === 'local') chosen = (it.localDescription != null) ? it.localDescription : '';
                    else if (choiceKey === 'remote') chosen = (it.remoteDescription != null) ? it.remoteDescription : '';
                    else {
                        // auto: remote preferred, then local, else null
                        if (it.remoteDescription != null && it.remoteDescription !== '') chosen = it.remoteDescription;
                        else if (it.localDescription != null && it.localDescription !== '') chosen = it.localDescription;
                        else chosen = null;
                    }
                    it.chosenDescription = chosen;
                    // rebuild visible line
                    if (!it.isComment && it.content) {
                        it.line = it.content + (chosen != null && chosen.length > 0 ? ('  # ' + chosen) : '');
                    }
                    // update preview text and re-render
                    preview.textContent = (chosen !== null && chosen !== undefined) ? (chosen || I18n.t('(geen)')) : (it.localDescription || it.remoteDescription || I18n.t('(geen)'));
                    setActiveByValue(chosen);
                    renderMergeList(window.mergeItems);
                    updateMergePreview(window.mergeItems);
                }

                [btnAuto, btnLocal, btnRemote, btnNone].forEach(b => b.addEventListener('click', () => applyChoice(b.dataset.key)));

                label.appendChild(chooser);
            }

            const badge = document.createElement('span');
            if (item.inLocal && item.inRemote) {
                badge.textContent = I18n.t('Beide');
                badge.className = 'merge-badge shared';
            } else if (item.inLocal) {
                badge.textContent = I18n.t('Alleen lokaal');
                badge.className = 'merge-badge';
            } else {
                badge.textContent = I18n.t('Alleen remote');
                badge.className = 'merge-badge remote';
            }

            row.appendChild(label);
            row.appendChild(badge);
            listEl.appendChild(row);
        });
    });

    updateMergeSummary(items);
    updateMergeToolbarCounts(items);
    updateMergePreview(items);
}

document.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.type !== 'checkbox') return;
    if (!target.closest || !target.closest('#mergeList')) return;

    const idx = Number(target.dataset.index);
    if (Number.isNaN(idx) || !window.mergeItems || !window.mergeItems[idx]) return;
    window.mergeItems[idx].selected = target.checked;
    updateMergeSummary(window.mergeItems);
    updateMergePreview(window.mergeItems);
});



// Compacte, duidelijke diff preview voor merge modal
function updateMergePreview(items) {
    const previewEl = document.getElementById('mergePreview');
    if (!previewEl) return;
    const pushPeer = !!document.getElementById('mergePushPeer')?.checked;

    const style = ''; // Styles live in manager.css.

    // Determine which items have any change (local or peer) given selection & pushPeer
    const relevant = items.map(item => {
        const localHas = !!item.inLocal;
        const remoteHas = !!item.inRemote;
        const keep = !!item.selected;

        // Determine the effective "after" description (what will be saved)
        const chosenDefined = (item.chosenDescription !== undefined && item.chosenDescription !== null);
        let afterDesc = '';
        if (chosenDefined) {
            afterDesc = item.chosenDescription || '';
        } else if (item.remoteDescription != null && String(item.remoteDescription).trim() !== '') {
            afterDesc = item.remoteDescription;
        } else if (item.localDescription != null && String(item.localDescription).trim() !== '') {
            afterDesc = item.localDescription;
        } else {
            afterDesc = '';
        }

        // Local action
        let localAction = 'Behouden';
        let localClass = 'unchanged';

        if (localHas && !keep) {
            localAction = I18n.t('Verwijderen'); localClass = 'del';
        } else if (!localHas && keep) {
            localAction = I18n.t('Toevoegen'); localClass = 'add';
        } else if (!localHas && !keep) {
            // Item not present locally and not kept -> no change locally
            localAction = 'Ongewijzigd'; localClass = 'unchanged';
        } else if (localHas && keep) {
            const localDescNow = (item.localDescription != null) ? String(item.localDescription) : '';
            const newDesc = afterDesc != null ? String(afterDesc) : '';
            if (localDescNow !== newDesc) {
                localAction = 'Wijzigen'; localClass = 'change';
            } else {
                localAction = 'Behouden'; localClass = 'unchanged';
            }
        }

        // Peer action (depends on pushPeer)
        let peerAction = 'Ongewijzigd';
        let peerClass = 'unchanged';
        if (pushPeer) {
            const remoteWillHave = keep;
            if (remoteHas && !remoteWillHave) { peerAction = I18n.t('Verwijderen'); peerClass = 'del'; }
            else if (!remoteHas && remoteWillHave) { peerAction = I18n.t('Toevoegen'); peerClass = 'add'; }
            else if (remoteHas && remoteWillHave) {
                const remoteNow = (item.remoteDescription != null) ? String(item.remoteDescription) : '';
                if (remoteNow !== (afterDesc != null ? String(afterDesc) : '')) { peerAction = 'Wijzigen'; peerClass = 'change'; }
                else { peerAction = 'Behouden'; peerClass = 'unchanged'; }
            } else { peerAction = remoteHas ? 'Behouden' : 'Ongewijzigd'; peerClass = 'unchanged'; }
        } else {
            peerAction = remoteHas ? 'Behouden' : (keep ? I18n.t('Niet gepusht') : 'Ongewijzigd');
            peerClass = 'unchanged';
        }

        const hasChange = localClass !== 'unchanged' || peerClass !== 'unchanged';
        return { item, localAction, localClass, peerAction, peerClass, hasChange };
    }).filter(r => r.hasChange);

    // Sort so that conflicts (shared items with differing descriptions) are shown first
    relevant.sort((a, b) => {
        const aConflict = a.item.inLocal && a.item.inRemote && (a.item.localDescription || '') !== (a.item.remoteDescription || '');
        const bConflict = b.item.inLocal && b.item.inRemote && (b.item.localDescription || '') !== (b.item.remoteDescription || '');
        if (aConflict !== bConflict) return aConflict ? -1 : 1;
        // Next, prefer items that remove from local (del) to show deletions before additions
        const orderVal = { 'del': 0, 'change': 1, 'add': 2, 'unchanged': 3 };
        const aVal = Math.min(orderVal[a.localClass] ?? 9, orderVal[a.peerClass] ?? 9);
        const bVal = Math.min(orderVal[b.localClass] ?? 9, orderVal[b.peerClass] ?? 9);
        if (aVal !== bVal) return aVal - bVal;
        return (a.item.content || '').localeCompare(b.item.content || '');
    });

    if (!relevant.length) {
        previewEl.innerHTML = style + I18n.fragment('<div class="merge-preview-empty">Geen wijzigingen om te tonen.</div>');
        return;
    }

    let html = I18n.fragment('<ul class="merge-preview-list">');
    for (const r of relevant) {
        const item = r.item;
        const safeContent = escapeHtml(item.content || (item.raw || ''));

        // Determine what the description was before (prefer local if present, else remote)
        const beforeDescRaw = (item.inLocal && item.localDescription != null) ? item.localDescription : ((item.inRemote && item.remoteDescription != null) ? item.remoteDescription : '');
        const beforeDesc = beforeDescRaw != null ? String(beforeDescRaw) : '';

        // Determine what the description will be after merge (user override via chosenDescription)
        const afterDescRaw = (item.chosenDescription !== undefined && item.chosenDescription !== null) ? item.chosenDescription : (item.remoteDescription != null ? item.remoteDescription : (item.localDescription != null ? item.localDescription : ''));
        const afterDesc = afterDescRaw != null ? String(afterDescRaw) : '';

        const titleAttr = (afterDesc ? `${safeContent} — ${escapeHtml(afterDesc)}` : safeContent);

        let subLine = '';
        // If description actually changes, show before → after. Otherwise show a single description if present.
        const norm = s => (s || '').trim();
        if (norm(beforeDesc) !== norm(afterDesc)) {
            const b = escapeHtml(beforeDesc || I18n.t('(geen)'));
            const a = escapeHtml(afterDesc || I18n.t('(geen)'));
            subLine = I18n.html`<div class="mp-sub"><span class="desc-before"># ${b}</span><span class="mp-arrow">→</span><span class="desc-after">${a}</span></div>`;
        } else if (afterDesc) {
            subLine = I18n.html`<div class="mp-sub"># ${escapeHtml(afterDesc)}</div>`;
        }

        html += I18n.html`<li class="merge-preview-row" title="${titleAttr}"><div class="merge-preview-content"><div class="mp-main">${safeContent}</div>${subLine}</div>` +
                I18n.html`<div class="merge-preview-actions"><span class="chip ${r.localClass}">Lok: ${r.localAction}</span><span class="chip ${r.peerClass}">Peer: ${r.peerAction}</span></div></li>`;
    }
    html += I18n.fragment('</ul>');

    const oldStyle = document.getElementById('merge-preview-style');
    if (oldStyle) oldStyle.remove();
    previewEl.innerHTML = style + html;
}

async function loadMergeData() {
    const statusEl = document.getElementById('mergeStatus');
    const previewEl = document.getElementById('mergePreview');
    const listEl = document.getElementById('mergeList');
    if (statusEl) statusEl.textContent = I18n.t('Merge data laden...');
    if (previewEl) previewEl.innerHTML = '';
    if (listEl) listEl.innerHTML = '';

    try {
        // Eerst lokale content ophalen via bestaande endpoint
        const localResult = await apiRequest('/api/sync/merge-data', {}, { timeoutSeconds: 8, retries: 0 });
        if (!localResult.ok || !localResult.data) {
            let msg = I18n.t('Fout bij laden van merge data.');
            if (localResult && localResult.error) msg += ' [' + localResult.error + ']';
            if (statusEl) statusEl.textContent = msg;
            return;
        }
        if (localResult.data.error) {
            let msg = I18n.t('Fout bij laden van merge data:') + ' ' + localResult.data.error;
            if (statusEl) statusEl.textContent = msg;
            return;
        }
        const localContent = localResult.data.localContent || '';


        // Remote ophalen via backend-proxy endpoint (POST)
        let remoteContent = '';
        let remoteError = null;
        const peerIp = localResult.data.peer;
        if (peerIp) {
            try {
                // Determine scheme and hostname for peer
                const scheme = appConfig.managerScheme || 'http';
                const port = appConfig.managerPort || 8080;
                const peerAddr = getPeerAddress(peerIp, scheme);
                const remoteUrl = `${scheme}://${peerAddr}:${port}`;
                // Use POST to backend proxy endpoint
                const remoteResult = await apiRequest('/api/merge/remote-preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        remote_url: remoteUrl,
                        verify: true
                    })
                }, { timeoutSeconds: 8, retries: 0 });
                if (remoteResult.ok && remoteResult.data && remoteResult.data.success) {
                    remoteContent = Array.isArray(remoteResult.data.whitelist) ? remoteResult.data.whitelist.join('\n') : (remoteResult.data.whitelist || '');
                } else {
                    remoteError = (remoteResult.data && remoteResult.data.error) || remoteResult.error || I18n.t('Onbekende fout');
                }
            } catch (err) {
                remoteError = err && err.message ? err.message : String(err);
            }
        }

        if (statusEl) statusEl.textContent = remoteError ? I18n.msg`Remote fout: ${remoteError}` : '';
        window.mergeLocalLines = parseWhitelistLines(localContent);
        window.mergeRemoteLines = parseWhitelistLines(remoteContent);
        const items = buildMergeItems(window.mergeLocalLines, window.mergeRemoteLines);
        window.mergeItems = items;
        initialMergeItems = JSON.stringify(items);
        renderMergeList(items);
        updateMergePreview(items);
    } catch (e) {
        if (statusEl) statusEl.textContent = I18n.t('Fout bij laden van merge data (JS):') + ' ' + (e && e.message ? e.message : e);
    }
}


async function applyMerge() {
    if (!requireRulesWritable()) return;
    const listEl = document.getElementById('mergeList');
    const applyBtn = document.getElementById('mergeApplyButton');
    const pushPeer = !!document.getElementById('mergePushPeer')?.checked;
    if (!listEl || !window.mergeItems) return;

    const checkboxes = listEl.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(box => {
        const idx = Number(box.dataset.index);
        if (!Number.isNaN(idx) && window.mergeItems[idx]) {
            window.mergeItems[idx].selected = box.checked;
        }
    });

    const mergedLines = window.mergeItems
        .filter(item => item.selected)
        .map(item => {
            // Rebuild line from content + chosenDescription to ensure UI overrides are applied
            if (item.isComment) {
                // preserve raw comment when present
                if (item.raw && item.raw.trim()) return item.raw.trim();
                return (item.chosenDescription != null && item.chosenDescription !== '') ? ('# ' + item.chosenDescription) : ('#');
            }
            const base = item.content || '';
            const desc = (item.chosenDescription !== undefined && item.chosenDescription !== null) ? item.chosenDescription : (item.description || '');
            return base + (desc != null && desc.length > 0 ? ('  # ' + desc) : '');
        });

    const content = mergedLines.join('\n') + (mergedLines.length ? '\n' : '');

    if (applyBtn) applyBtn.disabled = true;

    // Confirmation: show summary of additions/removals
    const localLines = Array.isArray(window.mergeLocalLines) ? window.mergeLocalLines.map(x => (x || '').trim()) : [];
    const mergedNorm = mergedLines.map(x => (x || '').trim());
    // Use normalized string comparison to avoid formatting differences causing false diffs
    const addedCount = mergedNorm.filter(l => !localLines.includes(l)).length;
    const removedCount = localLines.filter(l => !mergedNorm.includes(l)).length;
    const msgParts = [];
    if (addedCount) msgParts.push(`${addedCount} entries toevoegen`);
    if (removedCount) msgParts.push(I18n.msg`${removedCount} entries verwijderen`);
    const summaryMsg = msgParts.length ? msgParts.join(' • ') : I18n.t('Geen wijzigingen');
    const remoteLines = Array.isArray(window.mergeRemoteLines) ? window.mergeRemoteLines.map(x => (x || '').trim()) : [];
    const remoteAdded = mergedNorm.filter(l => !remoteLines.includes(l)).length;
    const remoteRemoved = remoteLines.filter(l => !mergedNorm.includes(l)).length;
    const remoteParts = [];
    if (remoteAdded) remoteParts.push(`${remoteAdded} op peer toevoegen`);
    if (remoteRemoved) remoteParts.push(I18n.msg`${remoteRemoved} op peer verwijderen`);
    const remoteSummary = remoteParts.length ? remoteParts.join(' • ') : I18n.t('Geen peer-wijzigingen');

    const pushLabel = pushPeer ? I18n.fragment('<span class="merge-badge shared" style="background:#d1fae5;color:#065f46;">JA</span>') : I18n.fragment('<span class="merge-badge remote" style="background:#fee2e2;color:#9b1c1c;">NEE</span>');
    const html = I18n.html`
        <div style="display:flex;flex-direction:column;gap:8px;">
            <div style="display:flex;gap:10px;align-items:center;"><strong>Lokale wijzigingen:</strong><div style="color:var(--ink)">${escapeHtml(summaryMsg)}</div></div>
            <div style="display:flex;gap:10px;align-items:center;"><strong>Peer wijzigingen:</strong><div style="color:var(--ink)">${escapeHtml(remoteSummary)}</div></div>
            <div style="display:flex;gap:10px;align-items:center;"><strong>Push naar peer:</strong><div>${pushLabel}</div></div>
            <div style="font-size:12px;color:var(--muted);">Bevestig om de geselecteerde regels op te slaan. Er wordt een backup gemaakt zodat je kunt herstellen.</div>
        </div>
    `;
    const confirmed = await showConfirmModal(html);
    if (!confirmed) {
        if (applyBtn) applyBtn.disabled = false;
        return;
    }

    // store backup to allow undo
    try {
        window.lastMergeBackup = (localLines.length ? localLines.join('\n') + '\n' : '');
    } catch (e) {
        window.lastMergeBackup = null;
    }

    try {
        const result = await apiRequest('/api/sync/merge-apply', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content,
                pushPeer
            })
        }, { timeoutSeconds: 10, retries: 0, suppressAuthPrompt: true, suppressUnauthorizedToast: true });

        // If the request was suppressed due to auth hardening (caller requested suppression),
        // avoid showing the standard 401 error toast and show a less alarming notification.
        if (result && result.status === 401 && result.authSuppressed) {
            showToast('warning', 'Merge informatie', 'Peer update geweigerd (401) — push overgeslagen vanwege authenticatie/hardening');
            const conflictWarning = document.getElementById('syncConflictWarning');
            if (conflictWarning) conflictWarning.remove();
            closeMergeModal();
            loadEntriesDebounced(300);
            refreshStatus();
            setTimeout(checkSyncConflicts, 600);
            return;
        }

        if (!result.ok || !result.data || !result.data.success) {
            throw new Error(result.error || I18n.t('Merge toepassen mislukt'));
        }

        if (result.data.peer_updated === false && result.data.peer_error) {
            showToast('warning', I18n.t('Merge opgeslagen'), `Peer update faalde: ${result.data.peer_error}`);
        } else if (result.data.peer_cleared === false && result.data.peer_clear_error) {
            showToast('warning', I18n.t('Merge opgeslagen'), `Conflict status op peer niet gewist: ${result.data.peer_clear_error}`);
        } else {
            showToast('success', I18n.t('Merge opgeslagen'), I18n.t('Whitelist is bijgewerkt en gesynchroniseerd'));
        }

        const conflictWarning = document.getElementById('syncConflictWarning');
        if (conflictWarning) conflictWarning.remove();

        closeMergeModal(true);
        loadEntriesDebounced(300);
        refreshStatus();
        // offer undo (restore) action after successful merge
        if (window.lastMergeBackup) {
            showUndoToast('success', I18n.t('Merge opgeslagen'), I18n.t('Whitelist is bijgewerkt'), 'Ongedaan maken', async () => {
                await restoreLastMergeBackup();
            });
        }
        setTimeout(checkSyncConflicts, 600);
    } catch (error) {
        showToast('error', I18n.t('Merge fout'), error.message);
    } finally {
        if (applyBtn) applyBtn.disabled = false;
    }
}

// Refresh node status
async function refreshStatus() {
    if (authEnabled && !isAuthenticated) {
        return;
    }
    try {
        if (statusPending) return;
        statusPending = true;
        const epoch = nodeStatusEpoch;
        const response = await fetch('/api/status', { cache: 'no-store', signal: AbortSignal.timeout(4000) });
        const result = { ok: response.ok, data: response.ok ? await response.json() : null };
        statusPending = false;
        if (epoch !== nodeStatusEpoch) { markNodeStatusUnknown(); return; }

        if (!result.ok || !result.data) {
            markNodeStatusUnknown();
            return;
        }

        const status = result.data;
        lastStatusAt = Date.now();
        clearTimeout(statusExpiry);
        statusExpiry = setTimeout(markNodeStatusUnknown, 12000);
        if (typeof status.canEditRules === 'boolean' && status.canEditRules !== appConfig.canEditRules) {
            appConfig.canEditRules = status.canEditRules;
            rulesWritable(); renderEntries();
        }
        const standalone = appConfig.deploymentMode === 'standalone';
        ['vipStatusText', 'peerStatusText'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.parentElement.hidden = standalone || (id === 'peerStatusText' && appConfig.managedHa);
        });
        document.getElementById('manualSyncButton').hidden = standalone || appConfig.managedHa || appConfig.canManageProxy === false;

        if (Object.hasOwn(status, 'whitelist_mtime')) {
            if (lastWhitelistMtime !== undefined && status.whitelist_mtime !== lastWhitelistMtime) {
                loadEntriesDebounced(300);
            }
            lastWhitelistMtime = status.whitelist_mtime;
        }

        // Update node role badge (vaste rol uit keepalived.conf priority)
        const roleBadge = document.getElementById('nodeRoleBadge');

        if (status.configured_role === 'STANDALONE') {
            roleBadge.className = 'node-badge master';
            roleBadge.textContent = I18n.t('ZELFSTANDIGE NODE');
        } else if (status.current_state === 'MASTER') {
            roleBadge.className = 'node-badge master';
            roleBadge.textContent = I18n.t('ACTIEVE NODE');
        } else if (status.current_state === 'BACKUP') {
            roleBadge.className = 'node-badge backup';
            roleBadge.textContent = I18n.t('STANDBY NODE');
        } else {
            roleBadge.className = 'node-badge unknown';
            roleBadge.textContent = I18n.t(status.current_state === 'FAULT' ? 'NODE IN FOUTTOESTAND' : 'UNKNOWN');
        }

        // Update VIP status indicator
        const vipDot = document.getElementById('vipStatusDot');
        const vipText = document.getElementById('vipStatusText');

        // VIP actief (groen) vs standby (geel)
        if (status.vip_active) {
            vipDot.className = 'status-dot active';
            vipText.textContent = I18n.msg`VIP ${status.vip_address} ACTIEF`;
        } else {
            vipDot.className = 'status-dot backup';
            vipText.textContent = `VIP ${status.vip_address} standby`;
        }

        // Toon actuele keepalived state (MASTER/BACKUP/FAULT/UNKNOWN)
        const currentStateText = document.createElement('small');
        currentStateText.style.marginLeft = '8px';
        currentStateText.style.color = 'var(--muted, #666)';
        currentStateText.style.fontSize = '12px';
        vipText.parentElement.title = `Current keepalived state: ${status.current_state || 'UNKNOWN'}`;

        // Update tinyproxy status
        const tinyproxyDot = document.getElementById('tinyproxyStatusDot');
        const tinyproxyText = document.getElementById('tinyproxyStatusText');

        tinyproxyDot.style.background = '';
        if (status.tinyproxy_active) {
            tinyproxyDot.className = 'status-dot active';
            if (['check_failed', 'unresponsive'].includes(status.tinyproxy_check_status)) {
                // Tinyproxy draait maar health check faalt
                tinyproxyText.textContent = I18n.t('⚠️ Tinyproxy draait maar health check faalt');
                tinyproxyDot.style.background = '#ffc107';
            } else {
                tinyproxyText.textContent = I18n.t('Tinyproxy actief ✓');
            }
        } else {
            tinyproxyDot.className = 'status-dot';
            tinyproxyText.textContent = I18n.t('✗ Tinyproxy niet actief');
        }

        // Update peer node status (other node online/offline)
        const peerDot = document.getElementById('peerStatusDot');
        const peerText = document.getElementById('peerStatusText');
        const peerAddress = status.peer_address || I18n.t('onbekend');

        if (status.peer_online === true) {
            peerDot.className = 'status-dot active';
            peerText.textContent = `Peer node ${peerAddress} online`;
        } else if (status.peer_online === false) {
            peerDot.className = 'status-dot';
            peerText.textContent = `Peer node ${peerAddress} offline`;
        } else {
            peerDot.className = 'status-dot backup';
            peerText.textContent = `Peer node ${peerAddress}: onbekend`;
        }

        if (status.peer_check_method) {
            peerText.parentElement.title = `Peer check method: ${status.peer_check_method}`;
        }

        // Split brain detection warning
        if (status.split_brain_warning) {
            const splitBrainWarning = document.getElementById('splitBrainWarning');
            if (!splitBrainWarning) {
                const notice = document.createElement('div');
                notice.id = 'splitBrainWarning';
                notice.style.cssText = `
                    background: #fff3cd;
                    color: #856404;
                    border: 1px solid #ffc107;
                    border-radius: 4px;
                    padding: 10px 15px;
                    margin-top: 10px;
                    display: flex;
                    gap: 8px;
                    align-items: center;
                `;
                notice.innerHTML = I18n.html`<span style="font-weight: bold;">⚠️ Mogelijke Split-Brain:</span> <span>Node rapporteert MASTER maar heeft VIP niet. Controleer keepalived-status!</span>`;
                document.querySelector('.status-bar').appendChild(notice);
            }
        } else {
            const warning = document.getElementById('splitBrainWarning');
            if (warning) {
                warning.remove();
            }
        }

        // Check sync conflicts (separate API call)
        checkSyncConflicts();

        // Update hostname
        if (status.hostname) {
            const hostnameEl = document.getElementById('nodeHostname');
            if (hostnameEl) {
                hostnameEl.textContent = status.hostname;
            }
        }

        // Last sync display is handled via sync state (checkSyncConflicts)

    } catch (error) {
        statusPending = false;
        markNodeStatusUnknown();
        // Fail silently - status refresh is niet kritisch
        console.warn('Status refresh gefaald:', error.message);
    }
}

// Load entries
// Debounced loadEntries - voorkom te snelle opeenvolgende reloads
function loadEntriesDebounced(delayMs = 300) {
    if (loadEntriesTimer) {
        clearTimeout(loadEntriesTimer);
    }

    loadEntriesTimer = setTimeout(() => {
        loadEntries();
    }, delayMs);
}

async function loadEntries() {
    if (authEnabled && !isAuthenticated) {
        return;
    }
    // Voorkom overlappende requests
    if (isLoadingEntries) {
        return;
    }

    isLoadingEntries = true;

    try {
        const result = await apiRequest('/api/entries', {}, { timeoutSeconds: 15, retries: 3, retryDelayMs: 1000 });

        if (result.ok && result.data && result.data.success) {
            entries = result.data.entries;
            entriesRevision = result.data.revision;
            if (typeof result.data.canEditRules === 'boolean') appConfig.canEditRules = result.data.canEditRules;
            rulesWritable();
            filterEntries();
            updateStats();

            // Reset failure counter on success
            consecutiveFailures = 0;

            // Clear error indicator als die er zou zijn
            const alertContainer = document.getElementById('alertContainer');
            if (alertContainer) {
                alertContainer.innerHTML = '';
            }
        } else {
            consecutiveFailures++;

            const errorMsg = result.error || (result.data && result.data.error) || I18n.t('Onbekende fout');
            console.error('[Entries Load Error]', result);
            showAlert('danger', I18n.msg`Fout bij laden entries: ${errorMsg}. Check authenticatie instellingen in Settings.`);
        }
    } catch (error) {
        consecutiveFailures++;

        handleApiError(error, I18n.t('entries laden'));
        showAlert('danger', I18n.t('Kan entries niet laden. Probeer opnieuw.'));
    } finally {
        isLoadingEntries = false;
    }
}

// Render entries
function renderEntries() {
 const container = document.getElementById('entriesList');
 const items = searchQuery ? filteredEntries : entries;
 container.innerHTML = items.length ? items.map(entry => RuleView.render(entry, !rulesWritable())).join('') : '<p class="help-text">' + escapeHtml(I18n.t(searchQuery ? 'Geen entries gevonden voor deze zoekopdracht' : 'Geen entries gevonden')) + '</p>';
}

// Filter entries based on search query
function filterEntries() {
    const searchInput = document.getElementById('searchInput');
    const clearBtn = document.getElementById('clearSearch');
    const searchInfo = document.getElementById('searchInfo');

    searchQuery = searchInput.value.toLowerCase().trim();

    // Show/hide clear button
    if (searchQuery) {
        clearBtn.style.display = 'block';
    } else {
        clearBtn.style.display = 'none';
    }

    // Filter entries
    if (searchQuery) {
        filteredEntries = entries.filter(entry => {
            // Search in content
            if (entry.content && entry.content.toLowerCase().includes(searchQuery)) {
                return true;
            }
            // Search in display
            if (entry.display && entry.display.toLowerCase().includes(searchQuery)) {
                return true;
            }
            // Search in description
            if (entry.description && entry.description.toLowerCase().includes(searchQuery)) {
                return true;
            }
            // Search in domain hint
            if (entry.domain_hint && entry.domain_hint.toLowerCase().includes(searchQuery)) {
                return true;
            }
            return false;
        });

        // Show search info
        searchInfo.textContent = I18n.msg`${filteredEntries.length} van ${entries.length} entries gevonden`;
        searchInfo.style.display = 'block';
    } else {
        filteredEntries = entries;
        searchInfo.style.display = 'none';
    }

    renderEntries();
}

// Clear search
function clearSearch() {
    document.getElementById('searchInput').value = '';
    filterEntries();
}

// Edit entry
// Try to convert a regex pattern back to a domain string if possible
function regexToDomain(regex) {
    if (!regex) return '';
    // IPv4: ^1\.2\.3\.4$
    let m = regex.match(/^\^([0-9]{1,3}(?:\\.[0-9]{1,3}){3})\$$/);
    if (m) return m[1].replace(/\\./g, '.');
    // IPv6: ^\[....\]$
    m = regex.match(/^\^\\\[([0-9a-fA-F:]+)\\\]\$$/);
    if (m) return m[1];
    // Domain style produced by domainToRegex: '(^|\\.)' + escapedDomain + '(:[0-9]+)?$'
    // Handle patterns starting with '(^|\.)' or '^(.+\.)' (both used for subdomain matching)
    if (regex.startsWith('(^|\\.)') || regex.startsWith('(^|\.)')) {
        let inner = regex.replace(/^\(\^\|\\\.\)/, '');
        const suffix = '(:[0-9]+)?$';
        if (inner.endsWith(suffix)) {
            inner = inner.slice(0, -suffix.length);
        }
        inner = inner.replace(/\\\./g, '.').replace(/\.\*/g, '*');
        return inner;
    }
    // Current format '^(.+\.)?domain\.tld' and transitional '^(.+\.)domain\.tld' (no ?).
    // Both start with '^(.+\.)'; strip the prefix plus an optional trailing '?'.
    if (regex.startsWith('^(.+\\.)') || regex.startsWith('^(.+.)')) {
        let inner = regex.replace(/^\^\(\.\+\\\.\)\??/, '');
        inner = inner.replace(/^\^\(\.\+\.\)\??/, '');
        const suffix = '(:[0-9]+)?$';
        if (inner.endsWith(suffix)) inner = inner.slice(0, -suffix.length);
        inner = inner.replace(/\\\./g, '.').replace(/\.\*/g, '*');
        return '*.' + inner.replace(/^\*\./, '');
    }
    // Backwards compat: old Python '^([A-Za-z0-9-]+\.)*domain\.tld(:[0-9]+)?$'
    const pyWildcardPfx = '^([A-Za-z0-9-]+\\.)*';
    if (regex.startsWith(pyWildcardPfx)) {
        let inner = regex.slice(pyWildcardPfx.length);
        const suffix = '(:[0-9]+)?$';
        if (inner.endsWith(suffix)) inner = inner.slice(0, -suffix.length);
        inner = inner.replace(/\\\./g, '.').replace(/\\-/g, '-');
        if (inner && inner.includes('.')) return '*.' + inner;
    }
    // Exact domain, possibly with escaped hyphens from old Python: '^domain\.tld(:[0-9]+)?$'
    const exactSuffix = '(:[0-9]+)?$';
    if (regex.startsWith('^') && !regex.startsWith('^(') && regex.endsWith(exactSuffix)) {
        let inner = regex.slice(1, -exactSuffix.length);
        inner = inner.replace(/\\\./g, '.').replace(/\\-/g, '-');
        if (/^[a-zA-Z0-9.-]+$/.test(inner) && inner.includes('.')) return inner;
    }
    return '';
}

function editEntry(id) {
    if (!requireRulesWritable()) return;
    const entry = entries.find(e => e.id === id);
    if (!entry) return;
    currentEditId = id;
    currentEditRevision = entriesRevision;
    currentEditType = entry.type;
    const editDomain = document.getElementById('editDomain');
    const editRegex = document.getElementById('editRegex');
    const editRegexGroup = document.getElementById('editRegexGroup');
    const editDescription = document.getElementById('editDescription');
    const editDescriptionGroup = document.getElementById('editDescriptionGroup');
    // Hide all fields by default
    if (editDomain) editDomain.value = '';
    if (editRegex) editRegex.value = '';
    if (editDescription) editDescription.value = '';
    if (editRegexGroup) editRegexGroup.style.display = 'none';
    // Hide domain group by default (the parent of editDomain is the group)
    let editDomainGroup = null;
    if (editDomain && editDomain.parentElement && editDomain.parentElement.classList.contains('form-group')) {
        editDomainGroup = editDomain.parentElement;
        editDomainGroup.style.display = 'none';
    }
    if (editDescriptionGroup) editDescriptionGroup.style.display = 'none';
    // Show only comment field for standalone comments
    if (entry.type === 'comment') {
        if (editDescriptionGroup) editDescriptionGroup.style.display = '';
        if (editDescription) editDescription.value = entry.content || '';
        if (editRegexGroup) editRegexGroup.style.display = 'none';
        if (editDomainGroup) editDomainGroup.style.display = 'none';
    } else {
        // Show domain/regex for other types
        if (editDomainGroup) editDomainGroup.style.display = '';
        if (editRegexGroup) editRegexGroup.style.display = '';
        editDomain.disabled = false;
        editRegex.disabled = false;
        let isIp = false;
        const domainHint = entry.domain_hint || '';
        if (entry.type === 'domain') {
            editDomain.value = domainHint || entry.content || '';
            editRegex.value = domainToRegex(editDomain.value || entry.content || '');
            isIp = isValidIp(editDomain.value || entry.content || '');
        } else if (entry.type === 'regex') {
            editRegex.value = entry.content;
            const domainGuess = domainHint || regexToDomain(entry.content) || '';
            editDomain.value = domainGuess;
            isIp = isValidIp(domainGuess);
        }
        if (isIp) {
            editRegex.value = domainToRegex(editDomain.value);
        }
        if (editDescriptionGroup) editDescriptionGroup.style.display = '';
        editDescription.value = entry.description || '';
    }
    document.getElementById('editPreview').innerHTML = '';
    document.getElementById('editPreview').className = 'domain-hint';
    document.getElementById('saveEditButton').disabled = false;
    document.getElementById('editModal').classList.add('show');
    initialEditFields = editFields();
    document.body.classList.add('modal-open');
    if (_editModalEscHandler) document.removeEventListener('keydown', _editModalEscHandler);
    _editModalEscHandler = function(e) { if (e.key === 'Escape') closeEditModal(); };
    document.addEventListener('keydown', _editModalEscHandler);
    validateEditFields();
}

let _editModalEscHandler = null;
let initialEditFields = '';

function editFields() {
    return JSON.stringify([...document.querySelectorAll('#editModal input, #editModal textarea, #editModal select')]
        .map(field => field.type === 'checkbox' ? field.checked : field.value));
}

// Save edit
async function saveEdit() {
    const editDomain = document.getElementById('editDomain');
    const editRegex = document.getElementById('editRegex');
    const editRegexGroup = document.getElementById('editRegexGroup');
    const descriptionInput = document.getElementById('editDescription');
    const saveButton = document.getElementById('saveEditButton');
    const autoRestart = document.getElementById('editAutoRestart').checked;
    const isComment = currentEditType === 'comment';

    // Voorkom dubbele submits (bijv. snel dubbelklikken of Enter-repeat).
    if (saveButton && saveButton.dataset.saving === 'true') {
        return;
    }

    if (saveButton) {
        saveButton.dataset.saving = 'true';
        saveButton.disabled = true;
    }

    let content = '';
    let entryType = currentEditType;
    let description = '';
    let isIp = false;
    if (isComment) {
        // For comments, use the description field as the comment editor
        content = descriptionInput ? descriptionInput.value.trim() : '';
        description = '';
    } else {
        if (!editDomain || !editRegex) return;
        description = descriptionInput ? descriptionInput.value.trim() : '';
        if (editDomain.value.trim()) {
            content = editDomain.value.trim();
            entryType = 'domain';
            isIp = isValidIp(content);
        } else if (editRegex.value.trim()) {
            content = editRegex.value.trim();
            entryType = 'regex';
        }
    }
    if (!content) {
        showToast('warning', I18n.t('Fout'), isComment ? I18n.t('Comment mag niet leeg zijn') : I18n.t('Domein of regex mag niet leeg zijn'));
        if (saveButton) {
            saveButton.dataset.saving = 'false';
            validateEditFields();
        }
        return;
    }
    // Frontend doet geen eigen domein/regex validatie meer; backend beslist
    try {
        const response = await fetchWithTimeout(`/api/entries/${currentEditId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content: content,
                auto_restart: autoRestart,
                description: description || null,
                is_comment: isComment,
                entry_type: entryType,
                revision: currentEditRevision
            })
        }, 20);
        if (!response.ok) {
            const errorData = await parseJsonSafe(response);
            const message = errorData && errorData.error
                ? errorData.error
                : `HTTP ${response.status}`;
            throw new Error(message);
        }
        const data = await response.json();
        if (data.success) {
            showToast('success', I18n.t('Bijgewerkt'), I18n.t('Entry succesvol bijgewerkt'));
            if (data.restart_queued) {
                showToast('info', 'Service restart', I18n.t('Herstart is gestart op de achtergrond'));
                pollTinyproxyStatus(10, I18n.t('Config herladen'), I18n.t('Tinyproxy config is herladen'));
            } else if (data.restart_attempted && data.restart_success === false) {
                showToast('warning', 'Service restart gefaald', data.restart_error);
            }
            closeEditModal(true);
            loadEntriesDebounced(500);
        } else {
            showToast('error', I18n.t('Fout bij bijwerken'), data.error);
        }
    } catch (error) {
        showToast('error', I18n.t('Kan entry niet bijwerken'), error.message);
        loadEntriesDebounced(1000);
    } finally {
        if (saveButton) {
            saveButton.dataset.saving = 'false';
            validateEditFields();
        }
    }
}
// Domein naar regex conversie (zelfde als addEntryForm)
function domainToRegex(domain) {
    if (!domain) return '';
    let d = domain.trim();
    if (isValidIp(d)) {
        // IP-adres: geen regex-wrapping, alleen exact match
        if (isValidIpv6(d)) {
            // IPv6: tussen [ ] in regex
            return `^\\[${d.replace(/([\\.*+?^${}()|[\]\/\\])/g, '\\$1')}\\]$`;
        }
        // IPv4
        return `^${d.replace(/\./g, '\\.')}$`;
    }
    if (d.startsWith('*.')) {
        // Wildcard: root domein EN alle subdomeinen (^(.+\.)?  == optionele subdomein-prefix)
        const base = d.slice(2).replace(/\./g, '\\.')
        return `^(.+\\.)?${base}(:[0-9]+)?$`;
    }
    // Exact root domein
    const escaped = d.replace(/\./g, '\\.')
    return `^${escaped}(:[0-9]+)?$`;
}

function isValidIp(ip) {
    return isValidIpv4(ip) || isValidIpv6(ip);
}
function isValidIpv4(ip) {
    return /^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/.test(ip.trim());
}
function isValidIpv6(ip) {
    // Eenvoudige IPv6 check (zonder []), accepteert ook :: notatie
    return /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/.test(ip.trim().replace(/^\[|\]$/g, ''));
}

// Synchronisatie en validatie voor edit-modal

function onEditDomainInput() {
    const editDomain = document.getElementById('editDomain');
    const editRegex = document.getElementById('editRegex');
    if (!editDomain || !editRegex) return;
    if (editDomain.value.trim()) {
        editRegex.value = domainToRegex(editDomain.value.trim());
    }
    validateEditFields();
}

function onEditRegexInput() {
    // When regex is edited, always try to update domain if possible
    const editDomain = document.getElementById('editDomain');
    const editRegex = document.getElementById('editRegex');
    if (!editDomain || !editRegex) return;
    const domainGuess = regexToDomain(editRegex.value.trim());
    editDomain.value = domainGuess;
    validateEditFields();
}

function validateEditFields() {
    const editDomain = document.getElementById('editDomain');
    const editRegex = document.getElementById('editRegex');
    const previewEl = document.getElementById('editPreview');
    const saveButtonEl = document.getElementById('saveEditButton');
    if (!editDomain || !editRegex || !previewEl || !saveButtonEl) return;
    const domain = editDomain.value.trim();
    const regex = editRegex.value.trim();
    // Domein/IP validatie
    if (domain) {
        if (isValidIpv4(domain)) {
            if (domain.length > 15) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> IPv4-adres te lang');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (/\s/.test(domain)) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> IP-adres bevat spaties');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
        } else if (isValidIpv6(domain)) {
            if (domain.length > 45) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> IPv6-adres te lang');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (/\s/.test(domain)) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> IP-adres bevat spaties');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
        } else {
            if (domain.length > 253) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Domeinnaam te lang (max 253 tekens)');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (/[^a-zA-Z0-9*.-]/.test(domain)) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Domeinnaam bevat ongeldige tekens');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (/\s/.test(domain)) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Domeinnaam bevat spaties');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (domain.startsWith('-') || domain.endsWith('-')) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Domeinnaam mag niet beginnen of eindigen met een streepje');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (domain.includes('..')) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Domeinnaam bevat dubbele punten');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (domain.includes('*') && !/^\*\.[^*]+$/.test(domain)) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Wildcard alleen toegestaan als "*.domein.tld"');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
            if (!/^([a-zA-Z0-9*][a-zA-Z0-9-]*\.)+[a-zA-Z]{2,}$/.test(domain.replace(/^\*\./, ''))) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Geen geldige FQDN');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
                return;
            }
        }
    }
    // Regex validatie
    if (regex) {
        if (regex.length > 256) {
            previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Regex te lang (max 256 tekens)');
            previewEl.className = 'domain-hint error';
            saveButtonEl.disabled = true;
            return;
        }
        if (/\(\?[!=]/.test(regex)) {
            previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Regex bevat lookahead/lookbehind (niet toegestaan)');
            previewEl.className = 'domain-hint error';
            saveButtonEl.disabled = true;
            return;
        }
        try {
            new RegExp(regex);
        } catch (e) {
            previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Regex is ongeldig.');
            previewEl.className = 'domain-hint error';
            saveButtonEl.disabled = true;
            return;
        }
    }
    // For non-comment entries, require a domain value
    if (currentEditType !== 'comment' && !domain) {
        previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Domeinnaam mag niet leeg zijn.');
        previewEl.className = 'domain-hint error';
        saveButtonEl.disabled = true;
        return;
    }
    // Allow if regexToDomain(regex) matches the domain, even if the regex is not exactly what domainToRegex(domain) would generate
    if (domain && regex) {
        const derived = regexToDomain(regex);
        if (derived && derived === domain) {
            previewEl.innerHTML = I18n.fragment('<strong>✓ Geldig</strong> Regex en domein komen semantisch overeen.');
            previewEl.className = 'domain-hint success';
            saveButtonEl.disabled = false;
            return;
        }
        // If not semantically matching, fall back to strict check
        if (domainToRegex(domain) !== regex) {
            previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Regex komt niet overeen met domein.');
            previewEl.className = 'domain-hint error';
            saveButtonEl.disabled = true;
            return;
        }
    }
    previewEl.innerHTML = I18n.fragment('<strong>✓ Geldig</strong>');
    previewEl.className = 'domain-hint success';
    saveButtonEl.disabled = false;
}

// Close edit modal
function closeEditModal(saved = false) {
    if (!saved && !confirmDiscard(editFields() !== initialEditFields)) return;
    document.getElementById('editModal').classList.remove('show');
    document.body.classList.remove('modal-open');
    if (_editModalEscHandler) {
        document.removeEventListener('keydown', _editModalEscHandler);
        _editModalEscHandler = null;
    }
    currentEditId = null;
}

// Validate edit input
async function validateEditInput() {
    const contentEl = document.getElementById('editContent');
    const previewEl = document.getElementById('editPreview');
    const saveButtonEl = document.getElementById('saveEditButton');

    // Safeguard: check of elementen bestaan
    if (!contentEl || !previewEl || !saveButtonEl) {
        console.warn('Validate elements not found in DOM');
        return;
    }

    const content = contentEl.value.trim();

    if (!content) {
        previewEl.innerHTML = '';
        previewEl.className = 'domain-hint';
        saveButtonEl.disabled = false;
        return;
    }

    // Voor comments: alleen basic inline validatie
    if (currentEditType === 'comment') {
        if (content.includes('\n')) {
            previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Comments mogen geen nieuwe regels bevatten');
            previewEl.className = 'domain-hint error';
            saveButtonEl.disabled = true;
        } else if (content.length > 500) {
            previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> Comment te lang (max 500 karakters)');
            previewEl.className = 'domain-hint error';
            saveButtonEl.disabled = true;
        } else {
            previewEl.innerHTML = I18n.fragment('<strong>✓ Comment is geldig</strong>');
            previewEl.className = 'domain-hint success';
            saveButtonEl.disabled = false;
        }
        return;
    }

    // Toon loading state
    previewEl.innerHTML = 'Valideren...';
    previewEl.className = 'domain-hint';

    try {
        const result = await apiRequest('/api/validate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content: content,
                is_comment: content.startsWith('#')
            })
        }, { timeoutSeconds: 8, retries: 1, retryDelayMs: 400 });

        if (result.ok && result.data) {
            const data = result.data;
            if (data.valid && data.generated_regex) {
                previewEl.innerHTML = I18n.fragment('<strong>✓ Wordt opgeslagen als:</strong><br>') + escapeHtml(data.generated_regex);
                previewEl.className = 'domain-hint success';
                saveButtonEl.disabled = false;
            } else if (!data.valid) {
                previewEl.innerHTML = I18n.fragment('<strong>✗ Fout:</strong> ') + escapeHtml(data.error || 'Validatie gefaald');
                previewEl.className = 'domain-hint error';
                saveButtonEl.disabled = true;
            } else {
                previewEl.innerHTML = '';
                previewEl.className = 'domain-hint';
                saveButtonEl.disabled = false;
            }
        } else {
            // Validatie gefaald, maar sta toe om toch op te slaan
            previewEl.innerHTML = I18n.fragment('<strong>⚠</strong> Validatie niet beschikbaar - opslaan op eigen risico');
            previewEl.className = 'domain-hint';
            saveButtonEl.disabled = false;
        }
    } catch (error) {
        // Graceful degradation: sta toe om op te slaan zonder validatie
        previewEl.innerHTML = I18n.fragment('<strong>⚠</strong> Validatie tijdelijk niet beschikbaar');
        previewEl.className = 'domain-hint';
        saveButtonEl.disabled = false;
    }
}

// Drag & drop handlers
function handleDragStart(event, entryId) {
    if (!requireRulesWritable() || isSavingOrder) { event.preventDefault(); return; }
    draggedEntryId = entryId;
    event.target.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/html', event.target.innerHTML);

    // Start auto-scroll check
    startAutoScroll();
}

function startAutoScroll() {
    const container = document.getElementById('entriesList');
    const scrollZone = 80; // pixels from edge to trigger scroll

    autoScrollInterval = setInterval(() => {
        if (draggedEntryId === null) {
            stopAutoScroll();
            return;
        }

        const rect = container.getBoundingClientRect();
        const mouseY = window.lastDragY || 0;

        // Calculate dynamic scroll speed based on distance from edge
        let scrollSpeed = 0;

        // Scroll up
        if (mouseY < rect.top + scrollZone && container.scrollTop > 0) {
            const distance = rect.top + scrollZone - mouseY;
            scrollSpeed = Math.min(300, Math.max(40, distance * 2));
            container.scrollTop -= scrollSpeed;
        }
        // Scroll down
        else if (mouseY > rect.bottom - scrollZone &&
                 container.scrollTop < container.scrollHeight - container.clientHeight) {
            const distance = mouseY - (rect.bottom - scrollZone);
            scrollSpeed = Math.min(300, Math.max(40, distance * 2));
            container.scrollTop += scrollSpeed;
        }
    }, 16); // ~60fps
}

function stopAutoScroll() {
    if (autoScrollInterval) {
        clearInterval(autoScrollInterval);
        autoScrollInterval = null;
    }
}

function handleDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';

    // Track mouse position for auto-scroll
    window.lastDragY = event.clientY;

    // Highlight drop target
    const target = event.currentTarget;
    if (target && !target.classList.contains('dragging')) {
        target.classList.add('drag-over');
    }

    return false;
}

function handleDragLeave(event) {
    event.currentTarget.classList.remove('drag-over');
}

function handleDrop(event, targetId) {
    if (!requireRulesWritable() || isSavingOrder) { event.preventDefault(); return; }
    event.preventDefault();
    event.currentTarget.classList.remove('drag-over');

    if (draggedEntryId === null || draggedEntryId === targetId) {
        return;
    }

    // Zoek indices van dragged en target entries
    const draggedIndex = entries.findIndex(e => e.id === draggedEntryId);
    const targetIndex = entries.findIndex(e => e.id === targetId);

    if (draggedIndex === -1 || targetIndex === -1) {
        return;
    }

    // Herorden lokaal
    const [draggedEntry] = entries.splice(draggedIndex, 1);
    entries.splice(targetIndex, 0, draggedEntry);

    // Render direct voor snelle feedback
    renderEntries();

    // Sla op naar backend
    saveOrder();

    return false;
}

function handleDragEnd(event) {
    event.target.classList.remove('dragging');
    // Remove all drag-over classes
    document.querySelectorAll('.drag-over').forEach(el => {
        el.classList.remove('drag-over');
    });
    draggedEntryId = null;

    // Stop auto-scrolling
    stopAutoScroll();
}

// Save entry order to backend
async function saveOrder() {
    if (!requireRulesWritable()) return;
    if (isSavingOrder) {
        return; // Voorkom dubbele saves
    }

    isSavingOrder = true;

    try {
        const entryIds = entries.map(e => e.id);

        // Toon loading indicator
        const statusDot = document.getElementById('statusDot');
        const originalClass = statusDot ? statusDot.className : null;
        if (statusDot) {
            statusDot.className = 'status-dot';
            statusDot.style.background = '#ffc107';
        }

        const response = await fetchWithTimeout('/api/entries/reorder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                entry_ids: entryIds,
                revision: entriesRevision,
                auto_restart: false
            })
        }, 10);

        const data = await response.json();

        if (data.success) {
            // Herlaad entries om IDs te synchroniseren met nieuwe volgorde
            loadEntriesDebounced(600);

            // Toon korte success indicator
            if (statusDot) {
                statusDot.style.background = '#28a745';
                setTimeout(() => {
                    if (statusDot && originalClass) {
                        statusDot.className = originalClass;
                        statusDot.style.background = '';
                    }
                }, 1000);
            }
        } else {
            showToast('error', I18n.t('Volgorde niet opgeslagen'), I18n.t(data.error || 'Onbekende fout'));
            loadEntriesDebounced(500); // Reload om te herstellen
            if (statusDot && originalClass) {
                statusDot.className = originalClass;
                statusDot.style.background = '';
            }
        }
    } catch (error) {
        showToast('error', I18n.t('Volgorde niet opgeslagen'), I18n.t(error.message));
        loadEntriesDebounced(500); // Reload om te herstellen

        const statusDot = document.getElementById('statusDot');
        if (statusDot) {
            statusDot.className = 'status-dot';
            statusDot.style.background = '';
        }
    } finally {
        isSavingOrder = false;
    }
}

// Delete entry
async function deleteEntry(id) {
    if (!requireRulesWritable()) return;
    const revision = entriesRevision;
    const confirmed = await showConfirmModal(I18n.t('Weet je zeker dat je deze entry wilt verwijderen?'));
    if (!confirmed) return;

    try {
        const result = await apiRequest(`/api/entries/${id}?auto_restart=true&revision=${encodeURIComponent(revision)}`, {
            method: 'DELETE'
        }, { timeoutSeconds: 30, retries: 2, retryDelayMs: 800 });

        if (result.ok && result.data && result.data.success) {
            showToast('success', I18n.t('Verwijderd'), I18n.t('Entry succesvol verwijderd'));
            if (result.data.restart_queued) {
                showToast('info', 'Service restart', I18n.t('Herstart is gestart op de achtergrond'));
                pollTinyproxyStatus(10, I18n.t('Config herladen'), I18n.t('Tinyproxy config is herladen'));
            } else if (result.data.restart_attempted && result.data.restart_success === false) {
                showToast('warning', 'Service restart gefaald', result.data.restart_error);
            }
            loadEntriesDebounced(500);
        } else if (result.ok && !result.data) {
            showToast('warning', 'Onvolledige response', I18n.t('Entry is mogelijk verwijderd, lijst wordt ververst'));
            loadEntriesDebounced(1000);
        } else {
            showToast('error', I18n.t('Fout bij verwijderen'), I18n.t(result.error || 'Onbekende fout'));
            loadEntriesDebounced(1000);
        }
    } catch (error) {
        showToast('error', I18n.t('Kan entry niet verwijderen'), error.message);
        loadEntriesDebounced(1000);
    }
}

// Restart service
async function restartService() {
    if (appConfig.canManageProxy === false) { requireRulesWritable(); return; }
    const confirmed = await showConfirmModal(I18n.t('Weet je zeker dat je de Tinyproxy service wilt herstarten?'));
    if (!confirmed) return;

    try {
        const result = await apiRequest('/api/service/restart', {
            method: 'POST'
        }, { timeoutSeconds: 25, retries: 1, retryDelayMs: 800 });

        if (result.ok && result.data) {
            if (result.data.success) {
                showToast('info', 'Service restart', result.data.message || I18n.t('Herstart is gestart'));
                pollTinyproxyStatus();
                setTimeout(refreshStatus, 2000);
            } else {
                showToast('error', I18n.t('Fout bij herstarten'), result.data.error || I18n.t('Service restart mislukt'));
            }
        } else {
            showToast('info', 'Service restart', I18n.t('Herstart is gestart, status wordt gecontroleerd'));
            pollTinyproxyStatus();
            setTimeout(refreshStatus, 2000);
        }
    } catch (error) {
        showToast('error', I18n.t('Kan service niet herstarten'), error.message);
    }
}

// Update stats
function updateStats() {
    const regex = entries.filter(e => e.type === 'regex').length;
    const comments = entries.filter(e => e.type === 'comment').length;
    for (const [id, count, singular, plural] of [
        ['totalEntries', entries.length - comments, 'Filterregel', 'Filterregels'],
        ['regexEntries', regex, 'Regex Pattern', 'Regex Patterns'],
        ['commentEntries', comments, 'Opmerking', 'Comment(s)']
    ]) {
        const value = document.getElementById(id);
        value.textContent = count;
        value.nextElementSibling.textContent = I18n.t(count === 1 ? singular : plural);
    }
}

// Show alert
function showAlert(type, message) {
    message = I18n.t(message);
    const container = document.getElementById('alertContainer');
    const alert = document.createElement('div');
    alert.className = `alert alert-${type} show`;
    alert.textContent = message;

    container.innerHTML = '';
    container.appendChild(alert);

    setTimeout(() => {
        alert.classList.remove('show');
        setTimeout(() => alert.remove(), 300);
    }, 5000);
}

// Show toast notification (modern alternative to showAlert)
function showToast(type, title, message) {
    title = I18n.t(title);
    message = I18n.t(message);
    const icons = {
        'success': '✓',
        'error': '✗',
        'warning': '⚠',
        'info': 'ℹ'
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = I18n.html`
        <span class="toast-icon">${icons[type] || 'ℹ'}</span>
        <div class="toast-content">
            <div class="toast-title">${escapeHtml(title)}</div>
            ${message ? I18n.html`<div class="toast-message">${escapeHtml(message)}</div>` : ''}
        </div>
        <span class="toast-close" onclick="this.parentElement.remove()">×</span>
    `;

    const container = document.getElementById('toastContainer');
    container.appendChild(toast);

    // Trigger animation
    setTimeout(() => toast.classList.add('show'), 10);

    // Auto remove after 5 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

// Restore previous merge backup
async function restoreLastMergeBackup() {
    if (!window.lastMergeBackup) {
        showToast('warning', I18n.t('Herstel mislukt'), I18n.t('Geen backup beschikbaar'));
        return;
    }
    try {
        const result = await apiRequest('/api/sync/merge-apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: window.lastMergeBackup, pushPeer: false })
        }, { timeoutSeconds: 10, retries: 0, suppressAuthPrompt: true });

        if (result && result.ok) {
            showToast('success', I18n.t('Hersteld'), I18n.t('Vorige versie is hersteld'));
            loadEntriesDebounced(300);
            refreshStatus();
            window.lastMergeBackup = null;
        } else {
            throw new Error(result && (result.error || JSON.stringify(result)) || I18n.t('Onbekende fout'));
        }
    } catch (e) {
        showToast('error', I18n.t('Herstel fout'), e.message || String(e));
    }
}

// Show toast with undo action
function showUndoToast(type, title, message, actionLabel, actionCb) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = I18n.html`
        <span class="toast-icon">✓</span>
        <div class="toast-content">
            <div class="toast-title">${escapeHtml(title)}</div>
            ${message ? I18n.html`<div class="toast-message">${escapeHtml(message)}</div>` : ''}
        </div>
        <div style="display:flex; gap:8px; align-items:center; margin-left:8px;">
            <button class="button button-secondary" style="padding:6px 10px;" id="undoAction">${escapeHtml(actionLabel)}</button>
            <span class="toast-close">×</span>
        </div>
    `;
    container.appendChild(toast);
    // wire up
    toast.querySelector('.toast-close').addEventListener('click', () => toast.remove());
    const actionBtn = toast.querySelector('#undoAction');
    if (actionBtn) actionBtn.addEventListener('click', () => { actionCb(); toast.remove(); });
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 300); }, 10000);
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Simple pluralization helper for Dutch
function pluralize(n, singular, plural) {
    return n === 1 ? `${n} ${singular}` : `${n} ${plural}`;
}

// Error classifier - geeft user-friendly error messages
function classifyError(error) {
    const message = error.message || String(error).toLowerCase();

    // Network errors
    if (message.includes('failed to fetch') || message.includes('networkerror')) {
        return {
            type: 'network',
            title: 'Verbindingsprobleem',
            message: 'Kan geen verbinding maken met de server. Controleer of de service draait.',
            retry: true
        };
    }

    // Timeout errors
    if (message.includes('timeout') || message.includes('aborted')) {
        return {
            type: 'timeout',
            title: 'Time-out',
            message: 'Server reageert niet op tijd. Probeer het nogmaals.',
            retry: true
        };
    }

    // HTTP errors
    if (message.includes('http 4')) {
        return {
            type: 'client_error',
            title: 'Ongeldige aanvraag',
            message: message,
            retry: false
        };
    }

    if (message.includes('http 5')) {
        return {
            type: 'server_error',
            title: 'Server fout',
            message: 'Er is een fout opgetreden op de server. Probeer het later opnieuw.',
            retry: true
        };
    }

    // Generic
    return {
        type: 'unknown',
        title: 'Onbekende fout',
        message: message,
        retry: false
    };
}

// Check network connectivity
async function checkNetworkConnectivity() {
    if (!navigator.onLine) {
        return false;
    }

    try {
        const response = await fetch('/api/status', {
            method: 'HEAD',
            cache: 'no-cache',
            credentials: 'include'
        });
        return response.ok || response.status < 500;
    } catch {
        return false;
    }
}

// Handle API error with user feedback
function handleApiError(error, context = '') {
    const classified = classifyError(error);
    const contextMsg = context ? ` (${context})` : '';

    console.error(`API Error${contextMsg}:`, error);

    // Check if offline
    if (!navigator.onLine) {
        showToast('warning', I18n.t('Geen internet'), I18n.t('Je bent offline. Controleer je netwerkverbinding.'));
        return;
    }

    showToast('error', classified.title, classified.message + contextMsg);
}

// Fetch with timeout helper
async function fetchWithTimeout(url, options = {}, timeoutSeconds = 10) {
    // Maak absolute URL als relative path
    const absoluteUrl = url.startsWith('http') ? url : (currentBaseUrl + url);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutSeconds * 1000);

    const headers = { ...options.headers };
    const isDirectConnection = isDirectConnectionUrl(currentBaseUrl);
    if (isDirectConnection) {
        console.debug(`[Direct Node] Requesting: ${absoluteUrl}`, {
            timeout: timeoutSeconds,
            isDirectNode: isDirectNode
        });
    }

    try {
        const response = await fetch(absoluteUrl, {
            ...options,
            headers,
            credentials: 'include',
            signal: controller.signal
        });

        if (isDirectConnection) {
            console.debug(`[Direct Node] Response status: ${response.status}`, {
                url: absoluteUrl,
                contentType: response.headers.get('content-type')
            });
        }

        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        if (isDirectConnection) {
            console.error(`[Direct Node] Request failed:`, {
                url: absoluteUrl,
                error: error.message,
                errorName: error.name
            });
        }
        if (error.name === 'AbortError') {
            throw new Error('Request timeout na ' + timeoutSeconds + 's');
        }
        throw error;
    }
}

async function fetchWithTimeoutPeer(absoluteUrl, timeoutSeconds = 6, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutSeconds * 1000);

    const headers = { ...(options.headers || {}) };

    try {
        const response = await fetch(absoluteUrl, {
            ...options,
            headers,
            credentials: 'include',
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            throw new Error('Request timeout na ' + timeoutSeconds + 's');
        }
        throw error;
    }
}

// Safe JSON parsing for empty/invalid responses
async function parseJsonSafe(response) {
    const text = await response.text();
    if (!text) {
        return null;
    }
    try {
        return JSON.parse(text);
    } catch (error) {
        return null;
    }
}

async function apiRequest(url, options = {}, config = {}) {
    // Directe verbindingen hebben langere timeouts en meer tolerantie nodig
    const isDirectConnection = isDirectConnectionUrl(currentBaseUrl);

    let timeoutSeconds = config.timeoutSeconds || 20;
    if (isDirectConnection) {
        timeoutSeconds = Math.max(30, timeoutSeconds); // Min 30s voor directe nodes
    }

    let retries = config.retries || 2;
    if (isDirectConnection) {
        retries = Math.max(4, retries); // Min 4 retries voor directe nodes
    }

    // A lost response may hide a committed write. Never replay mutations.
    if (!['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase())) retries = 0;
    const retryDelayMs = config.retryDelayMs || 600;

    for (let attempt = 0; attempt <= retries; attempt++) {
        try {
            if (isDirectConnection && attempt > 0) {
                console.debug(`[Direct Node] Retry attempt ${attempt}/${retries} for ${url}`);
            }

            const response = await fetchWithTimeout(url, options, timeoutSeconds);
            const data = await parseJsonSafe(response);

            if (response.status === 401 && authEnabled) {
                if (config.suppressUnauthorizedToast) {
                    return { ok: false, status: 401, data: null, authSuppressed: true };
                }
                if (!config.suppressAuthPrompt && new URL(url, window.location.href).origin === window.location.origin) {
                    isAuthenticated = false;
                    window.location.reload();
                }
                throw new Error(I18n.t('Authenticatie vereist'));
            }

            if (!response.ok) {
                return { ok: false, status: response.status, data, error: I18n.t(data?.error || `HTTP ${response.status}`) };
            }

            if (isDirectConnection) {
                console.debug(`[Direct Node] Success: ${url}`, { status: response.status, dataSize: data ? JSON.stringify(data).length : 0 });
            }

            // Success - reset failure counter
            consecutiveFailures = 0;

            return { ok: true, status: response.status, data };
        } catch (error) {
            const errorMsg = error.message || error.toString();

            if (errorMsg.toLowerCase().includes('authenticatie vereist')) {
                return { ok: false, status: 401, data: null };
            }

            if (isDirectConnection) {
                console.warn(`[Direct Node] Attempt ${attempt}/${retries} failed:`, {
                    url,
                    error: errorMsg,
                    willRetry: attempt < retries
                });
            }

            // Last attempt - increment failure counter
            if (attempt === retries) {
                consecutiveFailures++;

                if (isDirectConnection) {
                    console.error(`[Direct Node] All retries exhausted for ${url}`, {
                        totalAttempts: retries + 1,
                        consecutiveFailures,
                        lastError: errorMsg
                    });
                }
            }


            // Delay before retry (exponential backoff)
            if (attempt < retries) {
                const delayMs = retryDelayMs * Math.pow(1.5, attempt);
                await new Promise(resolve => setTimeout(resolve, delayMs));
                continue;
            }

            return { ok: false, status: null, data: null, error: I18n.t(errorMsg) };
        }
    }
}

async function pollTinyproxyStatus(maxAttempts = 10, successTitle = 'Service actief', successMessage = 'Tinyproxy is weer actief') {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            const response = await fetchWithTimeout('/api/service/status', {}, 10);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await parseJsonSafe(response);
            if (data && data.success && data.status && data.status.running) {
                showToast('success', successTitle, successMessage);
                return;
            }
        } catch (error) {
            // Ignore and retry
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
    showToast('warning', I18n.t('Service status onbekend'), I18n.t('Kon Tinyproxy status niet bevestigen'));
}

// Export whitelist
async function exportWhitelist() {
    // Default export path: use domain/wildcard cleaned output
    return exportWhitelistWithFormat();
}

// Open import modal
// Modal event handlers (set when modal opens so they can be removed on close)
let importInProgress = false;
let importApplied = false;
let _importPreviewExpanded = false;
const importDialog = document.getElementById('importModal');
const importForm = document.getElementById('importForm');
const importContent = document.getElementById('importContent');
const importMode = document.getElementById('importMode');
const importFile = document.getElementById('importFile');
const importButton = document.getElementById('importButton');
const importConfirmReplace = document.getElementById('importConfirmReplace');

function showImportFeedback(message = '', state = '') {
    const feedback = document.getElementById('importFeedback');
    feedback.dataset.state = state;
    feedback.textContent = message;
}

function setImportErrors(errors = []) {
    const details = document.getElementById('importErrors');
    details.hidden = !errors.length;
    details.open = false;
    document.getElementById('importErrorText').textContent = errors.join('\n');
}

function updateImportButton() {
    const replace = importMode.value === 'replace';
    document.getElementById('importReplaceNotice').hidden = !replace;
    importButton.disabled = importInProgress || importApplied || !importContent.value.trim() ||
        (replace && !importConfirmReplace.checked);
}

function updateImportDraft() {
    importApplied = false;
    importConfirmReplace.checked = false;
    showImportFeedback();
    setImportErrors();
    updateImportButton();
    previewImportContent();
}

function openImportModal() {
    if (!requireRulesWritable()) return;
    if (importDialog.open) return;
    importForm.reset();
    _importPreviewExpanded = false;
    updateImportDraft();
    importDialog.showModal();
}

protectDialogDismiss(importDialog, importDialog.querySelectorAll('[data-close-import]'),
    () => !importApplied && Boolean(importContent.value.trim()), () => importInProgress);
importContent.addEventListener('input', updateImportDraft);
importMode.addEventListener('change', updateImportDraft);
importConfirmReplace.addEventListener('change', updateImportButton);
importForm.addEventListener('submit', event => {
    event.preventDefault();
    if (!importButton.disabled) executeImport();
});
document.getElementById('openImportFile').addEventListener('click', () => importFile.click());
importFile.addEventListener('change', async () => {
    const [file] = importFile.files;
    importFile.value = '';
    if (!file) return;
    if (file.size > 1024 * 1024) {
        showImportFeedback(I18n.t('Kies een tekstbestand van maximaal 1 MiB.'), 'error');
        return;
    }
    if (!confirmDiscard(importContent.value.trim() && !importApplied)) return;
    const previous = importContent.value;
    try {
        const contents = await file.text();
        if (!importDialog.open || importInProgress || previous !== importContent.value) return;
        importContent.value = contents;
        updateImportDraft();
        importContent.focus();
    } catch {
        showImportFeedback(I18n.t('Het tekstbestand kon niet worden gelezen.'), 'error');
    }
});

function previewImportContent() {
    const contentEl = document.getElementById('importContent');
    const previewEl = document.getElementById('importPreview');
    const modeEl = document.getElementById('importMode');

    if (!contentEl || !previewEl) {
        return;
    }

    const raw = contentEl.value || '';
    const trimmed = raw.trim();
    if (!trimmed) {
        previewEl.hidden = true;
        previewEl.innerHTML = '';
        return;
    }

    const mode = modeEl ? modeEl.value : 'append';
    const lines = trimmed
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => line.length > 0);

    if (!lines.length) {
        previewEl.hidden = true;
        previewEl.innerHTML = '';
        return;
    }

    const currentLines = Array.isArray(entries)
        ? entries
            .map(entry => {
                if (!entry) return '';
                const line = entry.original || entry.display || entry.content || '';
                return String(line).trim();
            })
            .filter(line => line.length > 0)
        : [];
    const currentLineSet = new Set(currentLines);

    let added = [];
    let removed = [];

    if (mode === 'replace') {
        added = lines.filter(line => !currentLineSet.has(line));
        const importSet = new Set(lines);
        removed = currentLines.filter(line => !importSet.has(line));
    } else {
        const currentContentKeys = new Set(
            (entries || [])
                .filter(entry => entry && entry.type && entry.type !== 'comment')
                .map(entry => (entry.content || '').trim().toLowerCase())
                .filter(Boolean)
        );

        lines.forEach(line => {
            const parsed = parseWhitelistLine(line);
            if (parsed.isComment || !parsed.content) {
                added.push(line);
                return;
            }
            const key = parsed.content.toLowerCase();
            if (currentContentKeys.has(key)) {
                return;
            }
            currentContentKeys.add(key);
            added.push(line);
        });

        removed = [];
    }

    const total = lines.length;
    const commentCount = lines.filter(line => line.startsWith('#')).length;

    let inner = I18n.fragment('<div><strong>Verwachte wijzigingen</strong></div>');
    inner += `<div >${escapeHtml(I18n.msg`Regels: ${total}`)}`;
    if (commentCount) {
        inner += escapeHtml(I18n.msg`, commentaar: ${commentCount}`);
    }
    inner += I18n.html`</div>`;

    if (mode === 'replace') {
        inner += `<div >${escapeHtml(I18n.msg`Toevoegen: ${added.length}, verwijderen: ${removed.length}`)}</div>`;
    } else {
        inner += `<div >${escapeHtml(I18n.msg`Toevoegen (nieuw): ${added.length}`)}</div>`;
    }

    const maxSample = _importPreviewExpanded ? 200 : 8;
    const sampleAdded = added.slice(0, maxSample);
    const sampleRemoved = removed.slice(0, maxSample);

    if (sampleAdded.length) {
        inner += I18n.html`<div ><strong>Toevoegen</strong></div>`;
        inner += I18n.html`<pre>${sampleAdded.map(escapeHtml).join('\n')}${added.length > sampleAdded.length ? '\n...' : ''}</pre>`;
    }

    if (sampleRemoved.length) {
        inner += I18n.html`<div ><strong>Verwijderen</strong></div>`;
        inner += I18n.html`<pre>${sampleRemoved.map(escapeHtml).join('\n')}${removed.length > sampleRemoved.length ? '\n...' : ''}</pre>`;
    }

    const remaining = Math.max(0, (added.length + removed.length) - (sampleAdded.length + sampleRemoved.length));
    inner += I18n.html`<div >`;
    if (!_importPreviewExpanded && remaining > 0) {
        inner += I18n.html`<button class="button button-secondary" type="button" id="importPreviewToggle" onclick="toggleImportPreviewExpanded(true)">${escapeHtml(I18n.msg`Toon alles (${remaining} meer)`)}</button>`;
    } else if (_importPreviewExpanded && (added.length + removed.length) > 0) {
        inner += I18n.html`<button class="button button-secondary" type="button" id="importPreviewToggle" onclick="toggleImportPreviewExpanded(false)">Verberg volledige lijst</button>`;
    }
    inner += I18n.html`</div>`;

    previewEl.hidden = false;
    previewEl.innerHTML = inner;
}

function toggleImportPreviewExpanded(expand) {
    _importPreviewExpanded = !!expand;
    previewImportContent();
    document.getElementById("importPreviewToggle")?.focus();
}
async function executeImport() {
    importInProgress = true;
    document.getElementById('importFields').disabled = true;
    const closeButtons = importDialog.querySelectorAll('[data-close-import]');
    closeButtons.forEach(button => { button.disabled = true; });
    updateImportButton();
    importButton.textContent = I18n.t('Importeren...');
    showImportFeedback(I18n.t('Filterregels worden gecontroleerd en geïmporteerd…'));
    setImportErrors();

    try {
        const response = await fetchWithTimeout('/api/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: importContent.value.trim(),
                mode: importMode.value,
                auto_restart: document.getElementById('importAutoRestart').checked
            })
        }, 45);
        const data = await parseJsonSafe(response);
        if (Array.isArray(data?.errors)) setImportErrors(data.errors);
        if (!response.ok || !data?.success) {
            showImportFeedback(data?.error || I18n.t('Onbekende fout bij importeren'), 'error');
            return;
        }

        importApplied = true;
        document.getElementById('importPreview').hidden = true;
        let message = I18n.msg`${data.imported_count} entries geïmporteerd`;
        if (data.skipped_count > 0) {
            message += '. ' + I18n.msg`Overgeslagen: ${data.skipped_count}`;
        }
        if (data.restart_queued) {
            message += '. ' + I18n.t('Herstart is gestart op de achtergrond');
            pollTinyproxyStatus(10, I18n.t('Config herladen'), I18n.t('Tinyproxy config is herladen'));
        }
        showImportFeedback(message, 'success');
        loadEntriesDebounced(800);
    } catch {
        showImportFeedback(I18n.t('De import kon niet worden bevestigd. Controleer de filterregels voordat je het opnieuw probeert.'), 'error');
    } finally {
        importInProgress = false;
        document.getElementById('importFields').disabled = false;
        closeButtons.forEach(button => { button.disabled = false; });
        importButton.textContent = I18n.t('Importeren');
        updateImportButton();
        if (importApplied) importDialog.querySelector('.workspace-dialog-footer [data-close-import]').focus();
    }
}

document.getElementById('copyImportErrorsButton').addEventListener('click', async event => {
    const button = event.currentTarget;
    const text = document.getElementById('importErrorText').textContent;
    try {
        if (navigator.clipboard) {
            await navigator.clipboard.writeText(text);
        } else {
            const fallback = document.createElement('textarea');
            fallback.value = text;
            fallback.className = 'clipboard-fallback';
            importDialog.append(fallback);
            fallback.select();
            const copied = document.execCommand('copy');
            fallback.remove();
            button.focus();
            if (!copied) throw new Error();
        }
        showImportFeedback(I18n.t('Alle fouten gekopieerd naar klembord'), 'success');
    } catch {
        showImportFeedback(I18n.t('Kan niet naar klembord schrijven'), 'error');
    }
});

// ===== MODALS =====

// Settings Modal






// Node Connection Management


async function transferAuthAndRedirect(targetUrl) {
    try {
        const token = await getAuthTransferToken();
        if (token) {
            const consumeUrl = new URL('/api/auth/consume', targetUrl).toString();
            const response = await fetchWithTimeoutPeer(consumeUrl, 6, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ token })
            });

            if (response.ok) {
                window.location.href = targetUrl;
                return;
            }
        }
    } catch (error) {
        showToast('warning', 'Overstap zonder sessie', I18n.t('Kon sessie niet meenemen, log opnieuw in'));
    }

    window.location.href = targetUrl;
}

function updateConnectionStatus() {
    if (appConfig.deploymentMode === 'standalone' || appConfig.managedHa) {
        const status = document.getElementById('currentConnection');
        if (status) status.textContent = window.location.host;
        const mode = document.getElementById('connectionMode');
        if (mode) mode.closest('.form-group').hidden = true;
        return;
    }
    const currentHost = window.location.hostname;
    let mode = 'vip';
    if (currentHost && currentHost === nodeAddresses[0]) {
        mode = 'node0';
    } else if (currentHost && currentHost === nodeAddresses[1]) {
        mode = 'node1';
    }

    const statusEl = document.getElementById('currentConnection');
    if (statusEl) {
        statusEl.textContent = getConnectionLabel(mode) || I18n.t('onbekend');
    }

    // Update dropdown als modal open is
    const dropdownEl = document.getElementById('connectionMode');
    if (dropdownEl) {
        dropdownEl.value = mode;
    }
}

// Settings Management




async function initAuthStatus() {
    if (authEnabled) {
        try {
            const response = await fetchWithTimeout('/api/auth/me', {}, 5);
            const data = await parseJsonSafe(response);
            if (response.ok && data?.authenticated === false) {
                window.location.reload();
                return;
            }
        } catch (error) {
            console.warn('Could not check session status:', error);
        }
        document.getElementById('logoutButton').hidden = false;
    }
    isAuthenticated = true;
    startAuthenticatedSession();
}

async function logout() {
    if (!authEnabled) return;

    try {
        const response = await fetchWithTimeout('/api/auth/logout', {
            method: 'POST'
        }, 6);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        window.location.replace('/');
    } catch (error) {
        showToast('error', I18n.t('Uitloggen mislukt'), error.message);
    }
}

async function forceLogoutAll() {
    if (!authEnabled) return;
    const confirmed = await showConfirmModal(I18n.t('Alle actieve sessies worden uitgelogd. Doorgaan?'));
    if (!confirmed) return;

    const button = document.getElementById('forceLogoutButton');
    if (button) {
        button.disabled = true;
    }

    try {
        const response = await fetchWithTimeout('/api/auth/force-logout', {
            method: 'POST'
        }, 8);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        window.location.replace('/');
    } catch (error) {
        showToast('error', I18n.t('Uitloggen mislukt'), error.message);
    } finally {
        if (button) {
            button.disabled = false;
        }
    }
}



// Test directe node verbinding


// Close modals when clicking outside
document.addEventListener('click', (e) => {

    const mergeModal = document.getElementById('mergeModal');
    const exportModal = document.getElementById('exportModal');
    const editModal = document.getElementById('editModal');


    if (e.target === mergeModal) {
        closeMergeModal();
    }
    if (e.target === exportModal) {
        closeExportModal();
    }
    if (e.target === editModal) {
        closeEditModal();
    }
});
