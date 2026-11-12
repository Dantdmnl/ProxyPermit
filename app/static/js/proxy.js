'use strict';
(() => {
    const $ = id => document.getElementById(id);
    const examples = {
        Port: ['3128', 'Poort waarop de proxy luistert.'], Listen: ['127.0.0.1', 'Luisteradres; gebruik het LAN-adres voor netwerkclients.'],
        Bind: ['192.0.2.10', 'Bronadres voor uitgaande verbindingen.'], Allow: ['192.168.0.0/24', 'Client-IP, netwerk of domein. Volgorde is belangrijk.'],
        Deny: ['192.168.0.42', 'Clienttoegang weigeren. Volgorde is belangrijk.'], BasicAuth: ['gebruiker wachtwoord', 'Proxy-authenticatie, los van je beheerderslogin.'],
        ConnectPort: ['443', 'Herhaal voor meerdere poorten. 0 schakelt CONNECT uit.'], Filter: ['"/etc/tinyproxy/whitelist.txt"', 'Filterbestand. De domein-editor gebruikt het ingestelde WHITELIST_PATH.'],
        FilterDefaultDeny: ['Yes', 'Yes = allowlist, No = blocklist. Geldt voor de volledige lijst.'], FilterType: ['ere', 'ere, bre of fnmatch. De domein-editor ondersteunt ere.'],
        FilterURLs: ['No', 'Gebruik bij voorkeur domeinfiltering; HTTPS-paden zijn niet zichtbaar.'],
        Upstream: ['http proxy.example.com:8080 ".example.com"', 'http, socks4, socks5, of none "domein". Laatste passende regel wint.'],
        AddHeader: ['"X-Company" "Example"', 'Verzoekheader toevoegen voor HTTP-verkeer.'], Anonymous: ['"Host"', 'Alleen genoemde headers doorlaten; herhaal per header.'],
        ReversePath: ['"/app/" "http://192.0.2.20/"', 'Koppel een lokaal pad aan een backend.'], ReverseOnly: ['Yes', 'Schakel forward proxy uit bij een reverse-proxyopstelling.'],
        ReverseBaseURL: ['"https://proxy.example.com/"', 'Extern bereikbaar basisadres voor redirects.'], LogLevel: ['Connect', 'Critical, Error, Warning, Notice, Connect of Info.'],
        Timeout: ['600', 'Maximale inactiviteit in seconden.'], MaxClients: ['100', 'Maximum gelijktijdige clients.'], ErrorFile: ['403 "/usr/share/tinyproxy/403.html"', 'HTML-pagina voor een HTTP-foutcode.']
    };
    let activeContent = '';
    let schema = [], revision = '', original = '', csrf = document.querySelector('meta[name="csrf-token"]').content;
    let busy = false, filterRevision = '', filterOriginal = '';
    let canActivate = false, canEdit = false, staged = false, filterStaged = false;
    const dirty = () => $('config').value !== original;
    const filterDirty = () => $('filter').value !== filterOriginal;
    function message(text, error = false) { $('message').textContent = I18n.t(text); $('message').className = 'notice' + (error ? ' error' : ''); }
    function settingFeedback(text, error = false) {
        const feedback = $('setting-feedback');
        feedback.hidden = false;
        feedback.textContent = I18n.t(text);
        feedback.classList.toggle('error', error);
    }
    function renderDraft() {
        if (!$('draft-diff')) return;
        const changed = dirty() || filterDirty();
        const pending = staged || filterStaged;
        $('draft-state').textContent = I18n.t(changed ? 'Niet opgeslagen' : pending ? 'Concept opgeslagen' : 'Geen openstaande wijzigingen');
        $('draft-panel').dataset.state = changed ? 'dirty' : pending ? 'staged' : 'idle';
        $('draft-description').textContent = I18n.t(changed ? 'Je bewerkt een concept. Dit is nog niet opgeslagen of actief.' : pending
            ? 'Dit concept is opgeslagen. De proxy gebruikt nog de actieve versie totdat je publiceert.'
            : 'Er is geen conceptwijziging. De editor toont de actieve configuratie.');
        $('draft-filter-note').hidden = !(filterStaged || filterDirty());
        const left = activeContent.split('\n'), right = $('config').value.split('\n');
        const output = $('draft-diff'); output.replaceChildren();
        const line = (value, added) => {
            const element = document.createElement('span');
            element.className = added ? 'draft-added' : 'draft-removed';
            const safe = /^\s*(BasicAuth|Upstream)\b/i.test(value) ? value.trim().split(/\s+/)[0] + ' [redacted]' : value;
            element.textContent = (added ? '+ ' : '- ') + safe + '\n'; output.append(element);
        };
        if (left.join('\n') === right.join('\n')) output.textContent = I18n.t('Geen configuratieverschillen.');
        else if (left.length > 400 || right.length > 400) output.textContent = I18n.t('Groot configuratiebestand: controleer de volledige werkversie onder Native configuratie.');
        else {
            const matrix = Array.from({ length: left.length + 1 }, () => new Uint16Array(right.length + 1));
            for (let i = left.length - 1; i >= 0; i--) for (let j = right.length - 1; j >= 0; j--)
                matrix[i][j] = left[i] === right[j] ? 1 + matrix[i + 1][j + 1] : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
            let i = 0, j = 0;
            while (i < left.length || j < right.length) {
                if (i < left.length && j < right.length && left[i] === right[j]) { i++; j++; }
                else if (j < right.length && (i === left.length || matrix[i][j + 1] >= matrix[i + 1][j])) line(right[j++], true);
                else line(left[i++], false);
            }
        }
    }
    function draftFilterMode() {
        const settings = [...$('config').value.matchAll(/^\s*FilterDefaultDeny\s+(Yes|No|On|Off)\b/gim)];
        return /^(yes|on)$/i.test(settings.at(-1)?.[1] || 'No') ? 'allowlist' : 'blocklist';
    }
    function markDirty() {
        const changed = dirty() || filterDirty();
        renderDraft();
        const pending = staged || filterStaged;
        $('dirty').dataset.state = changed ? 'dirty' : pending ? 'staged' : 'idle';
        $('dirty').textContent = I18n.t(!revision ? 'Geen configuratie geladen' : changed ? 'Niet opgeslagen' : pending ? 'Concept opgeslagen' : 'Geen openstaande wijzigingen');
        $('config').readOnly = busy || !canEdit;
        $('filter').readOnly = busy || !canEdit || !filterRevision;
        $('insert').disabled = busy || !canEdit;
        $('filter-policy-review').disabled = busy || !canEdit || (!/^\s*Filter\s+\S/im.test($('config').value) || draftFilterMode() === $('filter-policy-mode').value);
        $('filter-policy-mode').disabled = busy || !canEdit;
        $('validate').disabled = busy || !revision;
        $('save').disabled = busy || !canEdit || !dirty();
        $('activate').disabled = busy || !canActivate || filterDirty() || !(dirty() || staged || filterStaged);
        $('filter-save').disabled = busy || !canEdit || !filterRevision || !filterDirty();
        $('publish-hint').hidden = !filterDirty();
        $('draft-save').disabled = $('save').disabled;
        $('draft-publish').disabled = $('activate').disabled;
        $('draft-discard').disabled = busy || !canEdit || !staged;
        $('draft-discard').hidden = !staged;
        $('draft-filter-discard').hidden = !filterStaged;
        $('draft-filter-discard').disabled = busy || !canEdit;
    }
    function statusList(title, items, style) {
        if (!items.length) return;
        const heading = document.createElement('strong'); heading.textContent = title;
        const list = document.createElement('ul'); list.className = style;
        items.forEach(item => { const li = document.createElement('li'); li.textContent = I18n.t(item); list.append(li); });
        $('validation').append(heading, list);
    }
    function showValidation(result) {
        $('validation').replaceChildren();
        statusList(I18n.t('Fouten'), result.errors || [], 'errors'); statusList(I18n.t('Aandachtspunten'), result.warnings || [], 'warnings');
    }
    async function request(url, method = 'GET', data) {
        const response = await fetch(url, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: data === undefined ? undefined : JSON.stringify(data) });
        if (response.status === 401 && !dirty() && !filterDirty()) window.location.reload();
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || I18n.t('Verzoek mislukt. Log zo nodig opnieuw in via Filterregels & status.'));
        return result;
    }
    async function work(action) {
        if (busy) return;
        const controls = document.querySelectorAll('button');
        busy = true; controls.forEach(b => { b.disabled = true; }); markDirty();
        try { await action(); } catch (error) { message(error.message, true); }
        finally { busy = false; controls.forEach(b => { b.disabled = false; }); markDirty(); }
    }
    function chooseDirective() {
        const spec = schema.find(item => item.name === $('directive').value);
        $('setting-feedback').hidden = true;
        document.querySelectorAll('[data-directive]').forEach(button => {
            button.toggleAttribute('data-selected', button.dataset.directive === spec.name);
        });
        const example = examples[spec.name] || [spec.kind === 'boolean' ? 'Yes' : '', I18n.t('Waarde in Tinyproxy-formaat.')];
        $('value').value = ''; $('value').placeholder = I18n.t(example[0]); $('hint').textContent = I18n.t(example[1]);
        $('value').type = spec.name === 'BasicAuth' ? 'password' : 'text';
        const options = spec.name === 'FilterDefaultDeny' ? [['', 'Kies een waarde'], ['Yes', 'Allowlist'], ['No', 'Blocklist']]
            : spec.kind === 'boolean' ? [['', 'Kies een waarde'], ['Yes', 'Aan'], ['No', 'Uit']]
            : spec.name === 'Upstream' ? [['http', 'HTTP'], ['socks4', 'SOCKS4'], ['socks5', 'SOCKS5'], ['none', 'Direct verbinden']] : [];
        $('choice').replaceChildren(...options.map(([value, label]) => new Option(I18n.t(label), value)));
        $('choice').hidden = !options.length;
        $('value').hidden = Boolean(options.length);
        document.querySelector('label[for="choice"]').hidden = $('choice').hidden;
        document.querySelector('label[for="value"]').hidden = $('value').hidden;
        $('upstream-fields').hidden = spec.name !== 'Upstream';
        $('upstream-target').value = '';
        $('upstream-site').value = '';
        updateUpstreamFields();
    }
    function updateUpstreamFields() {
        const direct = $('choice').value === 'none';
        $('upstream-target').hidden = direct;
        document.querySelector('label[for="upstream-target"]').hidden = direct;
        $('upstream-help').textContent = I18n.t(direct ? 'Vul de bestemming in die rechtstreeks moet verbinden.' : 'Laat leeg voor alle bestemmingen. De laatste passende regel wint.');
    }
    function upstreamValue() {
        const protocol = $('choice').value;
        const target = $('upstream-target').value.trim(), site = $('upstream-site').value.trim();
        if ((protocol === 'none' && !site) || (protocol !== 'none' && !/^[\w.:[\]-]+:\d+$/.test(target))
            || (site && !/^[\w.:[\]/-]+$/.test(site))) return '';
        return [protocol, protocol !== 'none' && target, site && '"' + site + '"'].filter(Boolean).join(' ');
    }
    function chooseSection() {
        $('directive').replaceChildren(...schema.filter(item => item.section === $('section').value).map(item => new Option(item.name, item.name)));
        chooseDirective();
    }
    async function load() {
        const result = await request('/api/proxy/config');
        activeContent = result.activeContent ?? result.content;
        $('access-safety-notice').textContent = I18n.t(result.accessSafety ? (result.accessSafetyValid ? 'Anti-lockout actief: vaste beheerregels en netwerkbindingen blijven beschermd.' : 'Het actieve bestand wijkt af van het beheerprofiel. Controleer de fouten en herstel de vaste beheerregels.') : 'Anti-lockout is nog niet ingesteld. Laat het beheerprofiel via de installer vastzetten.');
        csrf = result.csrfToken; schema = result.schema; revision = result.revision; original = result.content;
        canActivate = result.canActivate; canEdit = result.canEdit;
        staged = result.staged; filterStaged = result.filterStaged;
        $('discard').hidden = !result.draft;
        $('activation-hint').hidden = canActivate;
        $('config').value = original; markDirty(); showValidation(result);
        $('section').replaceChildren(...[...new Set(schema.map(item => item.section))].map(section => new Option(I18n.t(section), section)));
        chooseSection();
        $('summary').replaceChildren();
        const modes = { allowlist: 'Allowlist', blocklist: 'Blocklist', disabled: 'Uitgeschakeld' };
        const filtering = result.summary.filterMode !== 'disabled';
        $('filter-policy-mode').value = filtering ? result.summary.filterMode : 'allowlist';
        markDirty();
        $('filter-policy-hint').textContent = I18n.t(!canEdit ? 'Publish shared proxy policy on the primary HA node.' : filtering
            ? 'Wijzigingen zijn pas actief na publiceren. De bestaande regels worden niet gewist.'
            : 'Kies eerst een filterbestand om een allowlist of blocklist te gebruiken.');
        [['Filtering', modes[result.summary.filterMode]],
            ['Patroontype', filtering ? result.summary.filterType.toUpperCase() : '—'],
            ['Filterdoel', filtering ? (result.summary.filterURLs ? 'HTTP-URL’s' : 'Domeinen') : '—']].forEach(([label, value]) => {
            const card = document.createElement('div'), small = document.createElement('small'), strong = document.createElement('strong');
            small.textContent = I18n.t(label); strong.textContent = I18n.t(value); card.append(small, strong); $('summary').append(card);
        });
        message(staged || filterStaged ? 'Er staat een opgeslagen concept klaar om te publiceren.' : 'Bestand geladen. Wijzigingen zijn pas actief na een geslaagde activatie.');
    }
    $('filter-policy-mode').addEventListener('change', markDirty);
    $('filter-policy-review').addEventListener('click', () => {
        if (busy || !canEdit || !/^\s*Filter\s+\S/im.test($('config').value)) return;
        $('filter-policy-dialog').returnValue = '';
        $('filter-policy-confirm').checked = false;
        $('filter-policy-impact').textContent = I18n.t($('filter-policy-mode').value === 'allowlist'
            ? 'Je kiest allowlist: alleen bestemmingen die bij de regels passen, worden toegestaan.'
            : 'Je kiest blocklist: bestemmingen die bij de regels passen, worden geblokkeerd.');
        $('filter-policy-dialog').showModal();
    });
    $('filter-policy-cancel').addEventListener('click', () => $('filter-policy-dialog').close('cancel'));
    $('filter-policy-dialog').addEventListener('close', () => {
        if ($('filter-policy-dialog').returnValue !== 'apply' || !$('filter-policy-confirm').checked || !canEdit) return;
        const value = $('filter-policy-mode').value === 'allowlist' ? 'Yes' : 'No';
        const lines = $('config').value.split('\n').filter(line => !/^\s*FilterDefaultDeny\s+/i.test(line));
        if (lines.at(-1) === '') lines.pop();
        $('config').value = [...lines, 'FilterDefaultDeny ' + value, ''].join('\n');
        markDirty();
        message('Filtermodus aangepast in je concept. Controleer de regels en publiceer om te activeren.');
        $('draft-panel').scrollIntoView({ block: 'center' });
    });
    $('section').addEventListener('change', chooseSection); $('directive').addEventListener('change', chooseDirective);
    $('choice').addEventListener('change', updateUpstreamFields);
    $('config').addEventListener('input', markDirty);
    $('filter').addEventListener('input', markDirty);
    document.querySelectorAll('[data-directive]').forEach(button => button.addEventListener('click', () => {
        const spec = schema.find(item => item.name === button.dataset.directive);
        if (!spec) return;
        $('section').value = spec.section; chooseSection();
        $('directive').value = spec.name; chooseDirective();
        ($('choice').hidden ? $('value') : $('choice')).focus();
    }));
    $('insert').addEventListener('click', () => {
        const spec = schema.find(item => item.name === $('directive').value);
        if (!spec) return;
        const value = spec.name === 'Upstream' ? upstreamValue() : ($('choice').hidden ? $('value') : $('choice')).value.trim();
        if (!value || /[\r\n]/.test(value)) {
            return settingFeedback(spec.name === 'Upstream' ? 'Gebruik host:poort en eventueel een bestemming. Direct verbinden vereist een bestemming.' : 'Vul één regel met geldige argumenten in.', true);
        }
        const lines = $('config').value.split('\n'), pattern = new RegExp('^\\s*' + spec.name + '\\s+', 'i');
        if (spec.name === 'FilterDefaultDeny' && !lines.some(line => /^\s*Filter\s+\S/i.test(line))) {
            $('directive').value = 'Filter'; chooseDirective(); $('value').focus();
            return settingFeedback('Kies eerst een filterbestand om een allowlist of blocklist te gebruiken.', true);
        }
        const index = spec.repeatable ? -1 : lines.findIndex(line => pattern.test(line));
        if (index >= 0) lines[index] = spec.name + ' ' + value;
        else { if (lines[lines.length - 1] === '') lines.pop(); lines.push(spec.name + ' ' + value, ''); }
        $('config').value = lines.join('\n');
        markDirty();
        settingFeedback('Instelling toegevoegd aan je concept. Controleer voordat je opslaat.');
    });
    $('validate').addEventListener('click', () => work(async () => {
        const result = await request('/api/proxy/config/validate', 'POST', { content: $('config').value });
        showValidation(result); message(result.valid ? I18n.t('Configuratiestructuur is geldig.') : I18n.t('Los de fouten op voordat je opslaat.'), !result.valid);
    }));
    function save(activate) { return work(async () => {
        const content = $('config').value;
        await request('/api/proxy/config', 'PUT', { content, revision, activate });
        if (activate) {
            filterRevision = ''; filterOriginal = ''; $('filter').value = '';
            $('filter-discard').hidden = true;
            $('filter-mode').textContent = I18n.t('Niet geladen');
        }
        await load();
        message(activate ? 'Configuratie opgeslagen en Tinyproxy geactiveerd.' : 'Concept opgeslagen. De draaiende proxy blijft hetzelfde.');
    }); }
    $('draft-save').addEventListener('click', () => $('save').click());
    $('draft-publish').addEventListener('click', () => $('activate').click());
    $('draft-discard').addEventListener('click', () => $('discard').click());
    $('draft-filter-discard').addEventListener('click', async () => {
        if (!filterRevision) await work(loadFilter);
        if (filterRevision) $('filter-discard').click();
    });
    $('save').addEventListener('click', () => save(false));
    $('activate').addEventListener('click', () => {
        if (window.confirm(I18n.t('Configuratie publiceren? Tinyproxy wordt herstart en actieve verbindingen kunnen worden onderbroken.'))) save(true);
    });
    $('native-link').addEventListener('click', () => { $('native-config').open = true; });
    if (window.location.hash === '#native-config') $('native-config').open = true;
    $('reload').addEventListener('click', () => { if (!dirty() || window.confirm(I18n.t('Je concept verwerpen en het bestand opnieuw laden?'))) work(load); });
    async function loadFilter() {
        const result = await request('/api/proxy/filter');
        filterRevision = result.revision; filterOriginal = result.content; $('filter').value = result.content;
        filterStaged = result.staged;
        $('filter-discard').hidden = !result.draft;
        const modes = { allowlist: 'Allowlist', blocklist: 'Blocklist' };
        $('filter-mode').textContent = I18n.t(modes[result.filterMode]) + ' · ' + result.filterType.toUpperCase();
        showValidation(result); message('Filterbestand geladen.');
    }
    $('filter-load').addEventListener('click', () => {
        if (filterDirty() && !window.confirm(I18n.t('Je filterconcept verwerpen en opnieuw laden?'))) return;
        work(loadFilter);
    });
    $('filter-save').addEventListener('click', () => work(async () => {
        if (!filterRevision) throw new Error(I18n.t('Laad eerst het filterbestand.'));
        const content = $('filter').value;
        const result = await request('/api/proxy/filter', 'PUT', { content, revision: filterRevision });
        filterRevision = result.revision; filterOriginal = content; filterStaged = true;
        $('filter-discard').hidden = false; showValidation(result);
        message('Filterconcept opgeslagen. Publiceer om de regels toe te passen.');
    }));
    function discard(kind, version, reload) {
        if (!window.confirm(I18n.t('Wil je het opgeslagen concept verwerpen?'))) return;
        work(async () => {
            await request('/api/proxy/' + kind + '/draft', 'DELETE', { revision: version });
            await reload();
        });
    }
    $('discard').addEventListener('click', () => discard('config', revision, load));
    $('filter-discard').addEventListener('click', () => discard('filter', filterRevision, loadFilter));
    window.addEventListener('beforeunload', event => { if (dirty() || filterDirty()) { event.preventDefault(); event.returnValue = ''; } });
    work(load);
})();
