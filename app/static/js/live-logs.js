'use strict';

// Logging Modal
let allLogs = [];
let logAutoRefreshInterval = null;
let logAutoRefreshEnabled = false;
let logFilterDebounceTimer = null;   // echte debounce voor zoekveld
let logPageOffset = 0;
const logPageLimit = 1000;
const logScanLimitBase = 12000;
const logScanLimitMax = 120000;
let logTotalCount = 0;
let logHasMore = false;
let logAnchor = null;
let logPeriod = {since: '', until: ''};
let logRequestSequence = 0;
let logAutoScrollEnabled = true;     // auto-scroll naar beneden
let logRefreshIntervalMs = 2000;     // instelbaar refresh interval
let logIsLivePage = true;            // false zodra gebruiker pagineert
let logCompactMode = false;

const loggingDialog = document.getElementById('loggingModal');
document.querySelectorAll('[data-open-live-logs]').forEach(control => {
    control.addEventListener('click', event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        openLoggingModal();
    });
});
window.addEventListener('hashchange', openLogsFromNavigation);
document.addEventListener('DOMContentLoaded', openLogsFromNavigation);
let logServerQuery = '';
let logRequestPending = false;
protectDialogDismiss(loggingDialog, [], () => false, () => false);

loggingDialog.addEventListener('close', () => {
    if (loggingDialog.open) return;
    ++logRequestSequence;
    logRequestPending = false;
    clearInterval(logAutoRefreshInterval);
    logAutoRefreshInterval = null;
    clearTimeout(logFilterDebounceTimer);
    logFilterDebounceTimer = null;
    logAutoRefreshEnabled = false;
    if (window.location.hash === '#live-logs') {
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }
});

function openLogsFromNavigation() {
    if (window.location.hash === '#live-logs') openLoggingModal();
}

function openLoggingModal() {
    if (loggingDialog.open) return;
    loggingDialog.showModal();
    loggingDialog.querySelector('.modal-close-btn').focus();
    document.getElementById('logFilter').value = '';
    logServerQuery = '';
    logPeriod = {since: '', until: ''};
    document.getElementById('logPeriodForm').reset();
    allLogs = [];
    updateSearchFeedback(null, '');
    document.getElementById('logContainer').textContent = '';
    updateLogSummary([]);
    logAutoRefreshEnabled = true;
    logAutoScrollEnabled = true;
    logIsLivePage = true;
    applyLogCompactMode();
    updateLogTimeZoneBadge();
    // Herstel opgeslagen refresh interval
    logRefreshIntervalMs = parseInt(localStorage.getItem('logRefreshInterval') || '2000', 10);
    const intervalSelect = document.getElementById('logRefreshInterval');
    if (intervalSelect) intervalSelect.value = String(logRefreshIntervalMs);
    resetLogPagination();
    updateLiveBanner();
    updateAutoRefreshButton();
    updateAutoScrollButton();
    setLogLoadingState(false);
    refreshLogs('open');
    logAutoRefreshInterval = setInterval(() => refreshLogs('auto'), logRefreshIntervalMs);
    // Scroll-lock detectie: eenmalig registreren zodat filterLogs dit niet steeds overschrijft
    const logContainer = document.getElementById('logContainer');
    if (logContainer) {
        logContainer.onscroll = () => {
            const atTop = logContainer.scrollTop < 40;
            if (!atTop && logAutoScrollEnabled && logIsLivePage) {
                logAutoScrollEnabled = false;
                updateAutoScrollButton();
            } else if (atTop && !logAutoScrollEnabled) {
                logAutoScrollEnabled = true;
                updateAutoScrollButton();
            }
        };
    }
    initLogChipClickHandler();
}

function closeLoggingModal() {
    loggingDialog.close();
}

function toggleAutoRefresh() {
    logAutoRefreshEnabled = !logAutoRefreshEnabled;
    updateAutoRefreshButton();
    if (logAutoRefreshInterval) {
        clearInterval(logAutoRefreshInterval);
        logAutoRefreshInterval = null;
    }
    if (logAutoRefreshEnabled) {
        logAutoRefreshInterval = setInterval(() => refreshLogs('auto'), logRefreshIntervalMs);
    }
}

function updateAutoRefreshButton() {
    const btn = document.getElementById('autoRefreshBtn');
    if (!btn) return;
    if (logAutoRefreshEnabled) {
        btn.textContent = I18n.t('⏹️ Pauzeer');
        btn.removeAttribute('data-paused');
    } else {
        btn.textContent = I18n.t('▶️ Hervat');
        btn.setAttribute('data-paused', '');
    }
}

function handleRefreshIntervalChange() {
    const select = document.getElementById('logRefreshInterval');
    if (!select) return;
    logRefreshIntervalMs = parseInt(select.value, 10) || 2000;
    localStorage.setItem('logRefreshInterval', String(logRefreshIntervalMs));
    // Herstart interval met nieuwe waarde als auto-refresh aan staat
    if (logAutoRefreshEnabled) {
        if (logAutoRefreshInterval) clearInterval(logAutoRefreshInterval);
        logAutoRefreshInterval = setInterval(() => refreshLogs('auto'), logRefreshIntervalMs);
    }
}

function toggleAutoScroll() {
    logAutoScrollEnabled = !logAutoScrollEnabled;
    updateAutoScrollButton();
    if (logAutoScrollEnabled) scrollLogToTop();
}

function updateAutoScrollButton() {
    const btn = document.getElementById('autoScrollBtn');
    if (!btn) return;
    if (logAutoScrollEnabled) {
        btn.textContent = I18n.t('🔒 Scroll vergrendeld');
        btn.removeAttribute('data-paused');
    } else {
        btn.textContent = I18n.t('🔓 Scroll vrij');
        btn.setAttribute('data-paused', '');
    }
}

function toggleLogCompactMode() {
    logCompactMode = !logCompactMode;
    applyLogCompactMode();
}


function applyLogCompactMode() {
    const container = document.getElementById('logContainer');
    const btn = document.getElementById('logViewToggle');
    if (!container || !btn) return;
    if (logCompactMode) {
        container.classList.add('compact');
        btn.textContent = I18n.t('🔎 Detail');
    } else {
        container.classList.remove('compact');
        btn.textContent = I18n.t('🧭 Compact');
    }
}

function scrollLogToTop() {
    const container = document.getElementById('logContainer');
    if (container) container.scrollTop = 0;
}

// Ga terug naar live (pagina 1, auto-refresh hervat)
function goToLiveLogs() {
    logPeriod = {since: '', until: ''};
    document.getElementById('logPeriodForm').reset();
    logIsLivePage = true;
    logAutoScrollEnabled = true;
    updateAutoScrollButton();
    logServerQuery = '';
    const filterEl = document.getElementById('logFilter');
    if (filterEl) filterEl.value = '';
    updateSearchFeedback(null, '');
    updateLiveBanner();
    resetLogPagination(); // reset ook logTotalCount + logHasMore
    if (!logAutoRefreshEnabled) toggleAutoRefresh();
    refreshLogs('open');
}

function updateLiveBanner() {
    const banner = document.getElementById('logLiveBanner');
    if (!banner) return;
    banner.hidden = logIsLivePage;
    document.getElementById('autoRefreshBtn').disabled = !logIsLivePage;
}

// Download logs van huidige pagina als schone .txt (uit data-array, niet uit DOM)
function downloadLogs() {
    if (!allLogs || allLogs.length === 0) {
        setLogLoadingState(false, I18n.t('Er zijn geen logs op de huidige pagina'));
        return;
    }

    const service = document.getElementById('logService')?.value || 'all';
    const totalPages = logPageLimit > 0 ? Math.max(1, Math.ceil(logTotalCount / logPageLimit)) : 1;
    const currentPage = getCurrentLogPage();

    // Bouw schone tekst direct uit de data (geen HTML-artefacten)
    const lines = allLogs.map(log => {
        const ts = formatLogTimestamp(log.timestamp);
        const level = log.level || '';
        const svc = log.service || '';
        const msg = log.message || '';
        const meta = log.meta || {};
        const metaBits = [];
        if (meta.client_ip) metaBits.push(`ip=${meta.client_ip}`);
        if (meta.client_name) metaBits.push(`system=${meta.client_name}`);
        if (meta.host) metaBits.push(`host=${meta.port ? `${meta.host}:${meta.port}` : meta.host}`);
        const metaText = metaBits.length ? ` {${metaBits.join(', ')}}` : '';
        return `${ts} [${level}] (${svc})${metaText} ${msg}`;
    });

    const header = [
        `# ProxyPermit — Log export`,
        `# Service : ${service}`,
        `# Pagina  : ${currentPage} van ${totalPages}`,
        `# Regels  : ${allLogs.length} (van totaal ~${logTotalCount})`,
        `# Datum   : ${formatLogTimestamp(new Date().toISOString())}`,
        `# Tijdzone: ${getTimezoneLabel()}`,
        `# UTC range: ${logPeriod.since || 'retained history'} to ${logPeriod.until || logAnchor || 'now'}`,
        '# Privacy: personal data may be present. Store securely and delete under your retention policy.',
        totalPages > 1 ? `# Let op  : Dit zijn alleen de logs van pagina ${currentPage}. Gebruik paginering om andere pagina's te downloaden.` : null,
    ].filter(Boolean).join('\n') + '\n\n';

    const blob = new Blob([header + lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logs_${service}_p${currentPage}_${formatTimestampForFilename(new Date().toISOString())}.txt`;
    document.body.appendChild(a);
    a.click();
    URL.revokeObjectURL(url);
    a.remove();

    const msg = totalPages > 1
        ? I18n.msg`Pagina ${currentPage} van ${totalPages} gedownload (${allLogs.length} regels). Pagineer voor andere pagina's.`
        : I18n.msg`${allLogs.length} regels opgeslagen.`;
    setLogLoadingState(false, msg);
}

function updateSearchFeedback(resultCount, query) {
    const el = document.getElementById('logSearchFeedback');
    if (!el) return;
    el.hidden = !query || resultCount === null;
    if (el.hidden) {
        el.textContent = '';
    } else if (resultCount === 1) {
        el.textContent = I18n.msg`1 resultaat voor “${query}”`;
    } else {
        el.textContent = I18n.msg`${resultCount} resultaten voor “${query}”`;
    }
}

function resetLogPagination() {
    logAnchor = null;
    logPageOffset = 0;
    logTotalCount = 0;
    logHasMore = false;
    updateLogPaginationUI();
}

function handleLogServiceChange() {
    updateSearchFeedback(null, '');
    resetLogPagination();
    refreshLogs(logServerQuery ? 'search' : 'service');
}

function handleLogLevelChange() {
    handleLogServiceChange();
}

function handleLogFilterInput() {
    // Debounce: wacht 250ms na laatste toetsaanslag voor lokale filter,
    // zodat de UI snel reageert zonder de server te spammen.
    // Wis ook de zoekfeedback — die is alleen geldig na een expliciete server-zoekopdracht.
    updateSearchFeedback(null, '');
    if (logFilterDebounceTimer) clearTimeout(logFilterDebounceTimer);
    logFilterDebounceTimer = setTimeout(() => {
        filterLogs();
        logFilterDebounceTimer = null;
    }, 250);
}

function jumpToFirstLogMatch() {
    const textFilter = (document.getElementById('logFilter')?.value || '').trim();
    if (!textFilter || textFilter.length < 2) {
        const feedback = document.getElementById('logSearchFeedback');
        feedback.hidden = false;
        feedback.textContent = I18n.t('Gebruik minstens twee tekens om op de server te zoeken.');
        return;
    }
    logServerQuery = textFilter;
    logIsLivePage = false;
    if (logAutoRefreshEnabled) {
        if (logAutoRefreshInterval) { clearInterval(logAutoRefreshInterval); logAutoRefreshInterval = null; }
        logAutoRefreshEnabled = false;
        updateAutoRefreshButton();
    }
    updateLiveBanner();
    resetLogPagination();
    refreshLogs('search');
}

function handleLogFilterKeydown(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        jumpToFirstLogMatch();
    }
}

function goToPreviousLogPage() {
    if (logPageOffset <= 0) return;
    logPageOffset = Math.max(0, logPageOffset - logPageLimit);
    logIsLivePage = false;
    if (logAutoRefreshEnabled) {
        if (logAutoRefreshInterval) { clearInterval(logAutoRefreshInterval); logAutoRefreshInterval = null; }
        logAutoRefreshEnabled = false;
        updateAutoRefreshButton();
    }
    updateLiveBanner();
    refreshLogs('page');
}

function goToNextLogPage() {
    if (!logHasMore) return;
    logPageOffset += logPageLimit;
    logIsLivePage = false;
    if (logAutoRefreshEnabled) {
        if (logAutoRefreshInterval) { clearInterval(logAutoRefreshInterval); logAutoRefreshInterval = null; }
        logAutoRefreshEnabled = false;
        updateAutoRefreshButton();
    }
    updateLiveBanner();
    refreshLogs('page');
}

function updateLogPaginationUI() {
    const infoEl = document.getElementById('logPageInfo');
    const prevBtn = document.getElementById('logPrevPageBtn');
    const nextBtn = document.getElementById('logNextPageBtn');

    const totalPages = logPageLimit > 0 ? Math.max(1, Math.ceil(logTotalCount / logPageLimit)) : 1;
    const currentPage = getCurrentLogPage();
    const start = logTotalCount > 0 ? (logPageOffset + 1) : 0;
    const end = Math.min(logPageOffset + allLogs.length, logTotalCount);

    if (infoEl) {
        infoEl.textContent = I18n.msg`Pagina ${currentPage} van ${totalPages}  (${start}–${end} van ${logTotalCount})`;
    }
    if (prevBtn) {
        prevBtn.disabled = logPageOffset <= 0;
        prevBtn.style.opacity = prevBtn.disabled ? '0.5' : '1';
        prevBtn.style.cursor = prevBtn.disabled ? 'not-allowed' : 'pointer';
    }
    if (nextBtn) {
        nextBtn.disabled = !logHasMore;
        nextBtn.style.opacity = nextBtn.disabled ? '0.5' : '1';
        nextBtn.style.cursor = nextBtn.disabled ? 'not-allowed' : 'pointer';
    }
}

function updateLogSummary(logs) {
    const summaryEl = document.getElementById('logSummary');
    if (!summaryEl) return;
    const pageTotal = logs.length;
    const serverTotal = Number.isInteger(logTotalCount) ? logTotalCount : pageTotal;
    const totals = {
        DEBUG: 0,
        INFO: 0,
        WARNING: 0,
        ERROR: 0
    };
    const services = {};

    logs.forEach((log) => {
        const lvl = (log.level || 'INFO').toUpperCase();
        totals[lvl] = (totals[lvl] || 0) + 1;
        const svc = log.service || 'unknown';
        services[svc] = (services[svc] || 0) + 1;
    });

    const serviceParts = Object.keys(services).sort().map((svc) => {
        return I18n.html`<span class="log-summary-item">${escapeLogHtml(svc)} <strong>${services[svc]}</strong></span>`;
    }).join('');

    summaryEl.innerHTML = I18n.html`
        <span class="log-summary-item">Server totaal <strong>${serverTotal}</strong></span>
        <span class="log-summary-item">Pagina <strong>${pageTotal}</strong></span>
        <span class="log-summary-item">Debug <strong>${totals.DEBUG || 0}</strong></span>
        <span class="log-summary-item">Info <strong>${totals.INFO || 0}</strong></span>
        <span class="log-summary-item">Waarschuwing <strong>${totals.WARNING || 0}</strong></span>
        <span class="log-summary-item">Fout <strong>${totals.ERROR || 0}</strong></span>
        ${serviceParts}
    `;
}

function getCurrentLogPage() {
    return Math.floor(logPageOffset / logPageLimit) + 1;
}

function setLogLoadingState(isLoading, text = '', error = false) {
    document.getElementById('logLoadingStatus').dataset.state = error ? 'error' : '';
    document.getElementById('logLoadingSpinner').hidden = !isLoading;
    const label = document.getElementById('logLoadingStatusText');
    const next = text || I18n.t('Klaar');
    if (label.textContent !== next) label.textContent = next;
}

function getLogLoadingLabel(reason = 'page') {
    if (reason === 'search') return I18n.t('Zoeken...');
    if (reason === 'service') return I18n.t('Service logs laden...');
    if (reason === 'open') return I18n.t('Logs laden...');
    return I18n.msg`Pagina ${getCurrentLogPage()} wordt geladen...`;
}

async function refreshLogs(reason = 'page') {
    const logContainer = document.getElementById('logContainer');
    if (!loggingDialog.open || (reason === 'auto' &&
        (logRequestPending || logContainer.contains(document.activeElement) || logContainer.querySelector('details[open]')))) return;
    const requestId = ++logRequestSequence;
    logRequestPending = true;
    const showLoadingIndicator = reason !== 'auto';
    let failed = false;
    let failureMessage = '';
    if (showLoadingIndicator) {
        setLogLoadingState(true, getLogLoadingLabel(reason));
    }
    try {
        const selectedService = document.getElementById('logService')?.value || 'all';
        const selectedLevel = document.getElementById('logLevel')?.value || '';
        const query = logServerQuery;
        const requestedScanLimit = Math.min(
            logScanLimitMax,
            Math.max(logScanLimitBase, logPageOffset + (logPageLimit * 5))
        );
        let url = `/api/logs?limit=${logPageLimit}&offset=${logPageOffset}&service=${encodeURIComponent(selectedService)}&scan_limit=${requestedScanLimit}`;
        if (document.getElementById('logHealthChecks')?.checked) url += '&health=include';
        if (query) url += `&q=${encodeURIComponent(query)}`;
        if (selectedLevel) url += `&level=${encodeURIComponent(selectedLevel)}`;
        for (const [name,value] of Object.entries(logPeriod)) if (value) url += `&${name}=${encodeURIComponent(value)}`;
        if (!logIsLivePage && logAnchor) url += `&anchor=${encodeURIComponent(logAnchor)}`;

        const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
        const payload = await response.json();
        const data = response.ok ? payload : null;
        if (!response.ok && typeof payload.error === 'string') failureMessage = I18n.t(payload.error);
        if (requestId !== logRequestSequence) return;

        failed = !data;
        if (!failed) {
            allLogs = data.logs || [];
            const pagination = data.pagination || {};
            logAnchor = pagination.anchor || logAnchor;
            const coverage = document.getElementById('logCoverageFeedback');
            coverage.hidden = !data.coverage?.incomplete && !data.retention?.externalFileLogging;
            coverage.textContent = data.coverage?.incomplete ? I18n.t('De resultaten zijn onvolledig door een scanlimiet, onleesbare bron of beschadigde logregel. Kies een kortere periode of controleer de logbronnen.') : '';
            if (data.retention?.externalFileLogging) coverage.textContent += ' ' + I18n.t('Deze Tinyproxy-bestandslogs gebruiken een afzonderlijk rotatie- en bewaarbeleid van de beheerder.');
            logTotalCount = Number.isInteger(pagination.total) ? pagination.total : allLogs.length;
            logHasMore = Boolean(pagination.hasMore);
            logPageOffset = Number.isInteger(pagination.offset) ? pagination.offset : logPageOffset;

            // Zoekfeedback bijwerken
            if (reason === 'search' && document.getElementById('logFilter').value.trim() === query) {
                updateSearchFeedback(logTotalCount, query);
            } else if (!query) {
                updateSearchFeedback(null, '');
            }

            updateLogPaginationUI();
            filterLogs();

            // Auto-scroll alleen op live pagina met vergrendeling aan
            if (logIsLivePage && logAutoScrollEnabled) {
                scrollLogToTop();
            }
        }
    } catch {
        failed = true;
    } finally {
        if (requestId === logRequestSequence) {
            logRequestPending = false;
            const error = failed ? (failureMessage || I18n.t('Logs konden niet worden geladen. Probeer opnieuw.')) : '';
            setLogLoadingState(false, error, failed);
        }
    }
}

// Module-level constanten voor filterLogs (niet bij elke aanroep herdefiniëren)
const LOG_LEVEL_COLORS = {
    'DEBUG': 'var(--log-neutral)',
    'INFO': 'var(--log-info)',
    'WARNING': 'var(--log-warning)',
    'ERROR': 'var(--log-error)'
};
const LOG_LEVEL_ICONS = {
    'DEBUG': '🔍',
    'INFO': 'ℹ️',
    'WARNING': '⚠️',
    'ERROR': '❌'
};
const LOG_SERVICE_ICONS = {
    'manager': '🟦',
    'tinyproxy': '🟥',
    'proxy_traffic': '🟪',
    'keepalived': '🟩',
    'audit': '🛡️'
};
const LOG_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDateAsSyslog(dt, includeYear = false) {
    if (!(dt instanceof Date) || isNaN(dt.getTime())) return '';
    const mon = LOG_MONTHS[dt.getMonth()] || '???';
    const day = String(dt.getDate()).padStart(2, '0');
    const year = String(dt.getFullYear());
    const hh = String(dt.getHours()).padStart(2, '0');
    const mm = String(dt.getMinutes()).padStart(2, '0');
    const ss = String(dt.getSeconds()).padStart(2, '0');
    if (includeYear) {
        return `${mon} ${day} ${hh}:${mm}:${ss} ${year}`;
    }
    return `${mon} ${day} ${hh}:${mm}:${ss}`;
}

function formatDateAsFilename(dt) {
    if (!(dt instanceof Date) || isNaN(dt.getTime())) return '';
    const mon = LOG_MONTHS[dt.getMonth()] || '???';
    const day = String(dt.getDate()).padStart(2, '0');
    const year = String(dt.getFullYear());
    const hh = String(dt.getHours()).padStart(2, '0');
    const mm = String(dt.getMinutes()).padStart(2, '0');
    const ss = String(dt.getSeconds()).padStart(2, '0');
    return `${mon}_${day}_${year}_${hh}-${mm}-${ss}`;
}

function parseLogTimestampToDate(raw) {
    const value = (raw || '').trim();
    if (!value) return null;

    if (value.includes('T')) {
        const isoDate = new Date(value);
        if (!isNaN(isoDate.getTime())) return isoDate;
    }

    const pyMatch = value.match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:[\.,]\d+)?$/);
    if (pyMatch) {
        const pyDate = new Date(`${pyMatch[1]}T${pyMatch[2]}`);
        if (!isNaN(pyDate.getTime())) return pyDate;
    }

    const syslogMatch = value.match(/^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})(?:[\.,]\d+)?$/);
    if (syslogMatch) {
        const monIndex = LOG_MONTHS.indexOf(syslogMatch[1]);
        if (monIndex >= 0) {
            const year = new Date().getFullYear();
            const day = parseInt(syslogMatch[2], 10);
            const parts = syslogMatch[3].split(':').map((part) => parseInt(part, 10));
            if (parts.length === 3) {
                return new Date(year, monIndex, day, parts[0], parts[1], parts[2]);
            }
        }
    }

    return null;
}

function getTimezoneLabel() {
    const offsetMinutes = -new Date().getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? '+' : '-';
    const absMinutes = Math.abs(offsetMinutes);
    const hours = String(Math.floor(absMinutes / 60)).padStart(2, '0');
    const minutes = String(absMinutes % 60).padStart(2, '0');
    return `Local (UTC${sign}${hours}:${minutes})`;
}
function formatLogTimestamp(raw) {
    const value = (raw || '').trim();
    if (!value) return '??:??:??';

    const syslogMatch = value.match(/^([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})(?:[\.,]\d+)?$/);
    if (syslogMatch) return syslogMatch[1];

    const parsed = parseLogTimestampToDate(value);
    if (parsed) {
        const currentYear = new Date().getFullYear();
        const includeYear = parsed.getFullYear() !== currentYear;
        const formatted = formatDateAsSyslog(parsed, includeYear);
        return formatted || value;
    }

    return value;
}

function formatTimestampForFilename(raw) {
    const parsed = parseLogTimestampToDate(raw);
    if (parsed) {
        const formatted = formatDateAsFilename(parsed);
        return formatted || 'unknown';
    }
    const friendly = formatLogTimestamp(raw);
    if (!friendly) return 'unknown';
    return friendly.replace(/\s+/g, '_').replace(/:/g, '-');
}

function updateLogTimeZoneBadge() {
    const badge = document.getElementById('logTimeZoneBadge');
    if (!badge) return;
    badge.textContent = getTimezoneLabel();
}

// Highlight zoektekst in een log-regel (hoisted buiten filterLogs)
function highlightLogText(text, query) {
    if (!query) return escapeLogHtml(text);
    const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return text.split(new RegExp('(' + escapedQuery + ')', 'gi'))
        .map((part, index) => index % 2 ? '<mark>' + escapeLogHtml(part) + '</mark>' : escapeLogHtml(part))
        .join('');
}

function initLogChipClickHandler() {
    const container = document.getElementById('logContainer');
    if (!container || container.dataset.chipHandler === '1') return;
    container.dataset.chipHandler = '1';
    container.addEventListener('click', (evt) => {
        const chip = evt.target.closest('.log-chip');
        if (!chip) return;
        const value = chip.getAttribute('data-filter-value') || '';
        if (!value) return;
        const filterEl = document.getElementById('logFilter');
        if (filterEl) {
            filterEl.value = value;
            handleLogFilterInput();
        }
    });
}

function buildLogMetaChips(meta, query) {
    if (!meta) return '';
    const chips = [];

    if (meta.client_ip) {
        const value = String(meta.client_ip);
        chips.push(I18n.html`<button type="button" class="log-chip" data-filter-value="${escapeLogHtml(value).replaceAll('"', '&quot;')}">IP: ${highlightLogText(value, query)}</button>`);
    }
    if (meta.client_name) {
        const value = String(meta.client_name);
        chips.push(I18n.html`<button type="button" class="log-chip" data-filter-value="${escapeLogHtml(value).replaceAll('"', '&quot;')}">SYS: ${highlightLogText(value, query)}</button>`);
    }
    if (meta.host) {
        const hostValue = meta.port ? `${meta.host}:${meta.port}` : meta.host;
        const value = String(hostValue);
        chips.push(I18n.html`<button type="button" class="log-chip" data-filter-value="${escapeLogHtml(value).replaceAll('"', '&quot;')}">HOST: ${highlightLogText(value, query)}</button>`);
    }
    return chips.join(' ');
}

const LOG_AUDIT_LABELS = new Map([
    ['whitelist_add', 'Filterregel toegevoegd'],
    ['whitelist_update', 'Filterregel gewijzigd'],
    ['whitelist_delete', 'Filterregel verwijderd'],
    ['whitelist_reorder', 'Filterregels opnieuw geordend']
]);

function isRoutineProxyLog(log) {
    if (log.level !== 'INFO') return false;
    const message = (log.message || '').replace(/^\[file(?::[^\]]*)?\]\s*/, '');
    if (log.service === 'manager') return message === 'Tinyproxy restarted successfully';
    return log.service === 'tinyproxy' &&
        /^(?:(?:done )?sending stored logs|Reloading config file(?: finished| \()|Initializing tinyproxy|Shutting down\.|(?:Started|Stopped|Stopping|Starting) tinyproxy\.service\b|tinyproxy\.service: Deactivated successfully\.)/i.test(message);
}

function filterLogs() {
    const serviceFilter = document.getElementById('logService')?.value || 'all';
    const levelFilter = document.getElementById('logLevel')?.value || '';
    const textFilter = (document.getElementById('logFilter')?.value || '').trim().toLowerCase();

    const filtered = allLogs.filter(log => {
        const matchService = serviceFilter === 'all' || log.service === serviceFilter;
        const matchLevel = !levelFilter || log.level === levelFilter;
        const meta = log.meta || {};
        const metaText = Object.values(meta).map(v => String(v).toLowerCase()).join(' ');
        const rawTimestamp = String(log.timestamp || '').toLowerCase();
        const friendlyTimestamp = formatLogTimestamp(log.timestamp || '').toLowerCase();
        const matchText = !textFilter ||
            log.message?.toLowerCase().includes(textFilter) ||
            rawTimestamp.includes(textFilter) ||
            friendlyTimestamp.includes(textFilter) ||
            (metaText && metaText.includes(textFilter));
        return matchService && matchLevel && matchText;
    });

    const container = document.getElementById('logContainer');
    updateLogSummary(filtered);
    if (filtered.length === 0) {
        container.innerHTML = I18n.fragment('<div class="log-row log-empty">Geen logs gevonden</div>');
        updateLogPaginationUI();
        return;
    }

    const levelColors = LOG_LEVEL_COLORS;
    const levelIcons = LOG_LEVEL_ICONS;
    const serviceIcons = LOG_SERVICE_ICONS;

    const formatTimestamp = formatLogTimestamp;

    // Highlight zoektekst in de log-regel
    const renderRow = log => {
        const color = levelColors[log.level] || 'var(--log-neutral)';
        const icon = levelIcons[log.level] || '•';
        const serviceIcon = serviceIcons[log.service] || '◻️';
        const timestamp = formatTimestamp(log.timestamp);
        const highlighted = highlightLogText(log.message || '', textFilter);
        const metaChips = buildLogMetaChips(log.meta || {}, textFilter);
        const metaBlock = metaChips ? I18n.html`<span class="log-row-meta">${metaChips}</span>` : '';
        const levelLabel = escapeLogHtml(log.level || '');
        const serviceLabel = escapeLogHtml(log.service || '');
        const header = I18n.html`<div class="log-row-head"><span class="log-row-time">${escapeLogHtml(timestamp)}</span><span class="log-row-service">${serviceIcon} ${serviceLabel}</span><span class="log-row-level" style="color: ${color};">${icon} ${levelLabel}</span>${metaBlock}</div>`;
        const audit = log.audit;
        const auditLabel = LOG_AUDIT_LABELS.get(audit?.event);
        if (auditLabel && !textFilter) {
            const summary = escapeLogHtml(I18n.msg`${I18n.t(auditLabel)} door ${audit.username}`);
            return I18n.html`<div class="log-row">${header}<details class="log-disclosure"><summary>${summary}</summary><div class="log-row-msg log-audit-detail">${highlighted}</div></details></div>`;
        }
        return I18n.html`<div class="log-row">${header}<div class="log-row-msg">${highlighted}</div></div>`;
    };

    const rows = [];
    for (let start = 0; start < filtered.length;) {
        const first = filtered[start];
        let end = start + 1;
        if (!textFilter && isRoutineProxyLog(first)) {
            while (end < filtered.length && filtered[end].timestamp === first.timestamp && isRoutineProxyLog(filtered[end])) end++;
        }
        if (end - start === 1) {
            rows.push(renderRow(first));
        } else {
            const timestamp = escapeLogHtml(formatTimestamp(first.timestamp));
            const label = escapeLogHtml(I18n.msg`Tinyproxy · ${end - start} routineberichten`);
            const details = filtered.slice(start, end).map(renderRow).join('');
            rows.push(I18n.html`<details class="log-row log-disclosure"><summary>${timestamp} · ${label}</summary>${details}</details>`);
        }
        start = end;
    }
    container.innerHTML = rows.join('');

    updateLogPaginationUI();
}

// `clearLogs()` removed — clearing the in-memory page view is no longer exposed in the UI.

function escapeLogHtml(text) {
    const element = document.createElement('div');
    element.textContent = text;
    return element.innerHTML;
}

document.getElementById('logPeriodForm').addEventListener('submit', event => {
    event.preventDefault();
    const since = document.getElementById('logSince').value;
    const until = document.getElementById('logUntil').value;
    if ((!since && !until) || (since && until && new Date(since) > new Date(until)) ||
        (until && new Date(until) > new Date())) {
        setLogLoadingState(false, I18n.t('Kies een geldige periode in het verleden.'), true);
        return;
    }
    logPeriod = {since: since ? new Date(since).toISOString() : '', until: until ? new Date(until).toISOString() : ''};
    logIsLivePage = false;
    if (logAutoRefreshEnabled) toggleAutoRefresh();
    resetLogPagination();
    updateLiveBanner();
    refreshLogs('search');
});
document.getElementById('logClearPeriod').addEventListener('click', goToLiveLogs);
