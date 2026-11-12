// ===== EXPORT MODAL LOGIC =====
function exportLineToDomain(line) {
    line = (line || '').trim();
    if (!line) return '';
    // Preserve full-line comments
    if (line.startsWith('#')) return line;

    // Separate inline comment (e.g. "...$ # VIP") and keep it to append later
    const cidx = line.indexOf('#');
    const comment = cidx !== -1 ? ' ' + line.slice(cidx).trim() : '';
    let core = cidx !== -1 ? line.slice(0, cidx).trim() : line;

    // If this is an IP-style regex, keep as regex (don't try to convert)
    if (/(?:\d{1,3}\\\.){3}\d{1,3}/.test(core) || /(?:\d{1,3}\.){3}\d{1,3}/.test(core)) {
        return core + comment;
    }

    // Strip anchors first (remove leading ^ and trailing $ characters)
    core = core.replace(/^\^/, '').replace(/\$$/, '');

    // Remove optional port groups like '(:[0-9]+)?' (may be escaped in the stored regex)
    core = core.replace(/(?:\\)?\(:[^)]*\)\?\s*$/, '');

    // Unescape backslashes so '\.' becomes '.' for easier parsing
    const raw = core.replace(/\\/g, '');

    // Detect wildcard marker '(.+.)' which comes from '^(.+\.)domain\.tld$' after unescaping
    let isWildcard = false;
    let domainCandidate = raw;
    const wcMatch = raw.match(/^\(\.\+\.\)(.+)$/);
    if (wcMatch) {
        isWildcard = true;
        domainCandidate = wcMatch[1];
    }

    // If domainCandidate now looks like a plain domain, return it (with wildcard if detected)
    if (/^[a-zA-Z0-9.-]+\.[a-zA-Z0-9-]{2,}$/.test(domainCandidate)) {
        const out = isWildcard ? '*.' + domainCandidate : domainCandidate;
        return out + comment;
    }

    // Fallbacks: try to extract domain-like parts using simpler patterns
    // e.g. '(.+.)adobe.com' -> adobe.com
    const simple = raw.replace(/^\(\.\+\.\)/, '').replace(/^\(\^\|\.\)/, '');
    if (/^[a-zA-Z0-9.-]+\.[a-zA-Z0-9-]{2,}$/.test(simple)) {
        return (raw.startsWith('(.+.)') ? '*.' : '') + simple + comment;
    }

    // If already plain domain or wildcard in the stored form, return it
    if (/^\*\.[a-zA-Z0-9.-]+\.[a-zA-Z0-9-]{2,}$/.test(core) || /^[a-zA-Z0-9.-]+\.[a-zA-Z0-9-]{2,}$/.test(core)) {
        return core + comment;
    }

    // Otherwise give up and return original (likely a complex regex)
    return line;
}

let _exportModalEscHandler = null;

// Open the export modal
function openExportModal() {
    if (appConfig.canManageProxy === false) { requireRulesWritable(); return; }
    const modal = document.getElementById('exportModal');
    if (modal) modal.classList.add('show');
    document.body.classList.add('modal-open');
    const fmt = document.getElementById('exportFormat');
    if (fmt) fmt.value = 'domain';
    if (_exportModalEscHandler) document.removeEventListener('keydown', _exportModalEscHandler);
    _exportModalEscHandler = function(e) { if (e.key === 'Escape') closeExportModal(); };
    document.addEventListener('keydown', _exportModalEscHandler);
}

// Close the export modal
function closeExportModal() {
    const modal = document.getElementById('exportModal');
    if (modal) modal.classList.remove('show');
    document.body.classList.remove('modal-open');
    if (_exportModalEscHandler) {
        document.removeEventListener('keydown', _exportModalEscHandler);
        _exportModalEscHandler = null;
    }
}

// Export with chosen format (regex or domain/wildcard)
async function exportWhitelistWithFormat() {
    if (appConfig.canManageProxy === false) { requireRulesWritable(); return; }
    const btn = document.getElementById('exportDownloadBtn');
    try {
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Exporteren...'; }
        const fmt = document.getElementById('exportFormat')?.value || 'domain';
        showToast('info', 'Exporteren...', I18n.t('Whitelist wordt geëxporteerd'));

        if (!navigator.onLine) {
            showToast('error', I18n.t('Geen verbinding'), I18n.t('Je bent offline. Exporteren vereist een actieve verbinding.'));
            return;
        }

        const response = await fetchWithTimeout('/api/export?format=' + encodeURIComponent(fmt), {}, 15);
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText || 'Export gefaald'}`);
        }

        const text = await response.text();
        let exportText = text;
        if (fmt === 'domain') {
            exportText = text.split(/\r?\n/).map(line => exportLineToDomain(line)).join('\n');
        }

        const blob = new Blob([exportText], { type: 'text/plain;charset=utf-8' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `whitelist_export_${fmt}_${formatTimestampForFilename(new Date().toISOString())}.txt`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        closeExportModal();
        showToast('success', 'Geëxporteerd', I18n.t('Whitelist succesvol gedownload'));
    } catch (error) {
        handleApiError(error, 'export');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = I18n.t('Downloaden'); }
    }
}
