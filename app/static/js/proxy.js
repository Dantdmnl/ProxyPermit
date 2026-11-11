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
    function markDirty() {
        const changed = dirty() || filterDirty();
        const pending = staged || filterStaged;
        $('dirty').dataset.state = changed ? 'dirty' : pending ? 'staged' : 'idle';
        $('dirty').textContent = I18n.t(!revision ? 'Geen configuratie geladen' : changed ? 'Niet opgeslagen' : pending ? 'Concept opgeslagen' : 'Geen openstaande wijzigingen');
        $('config').readOnly = busy || !canEdit;
        $('filter').readOnly = busy || !canEdit || !filterRevision;
        $('insert').disabled = busy || !canEdit;
        $('validate').disabled = busy || !revision;
        $('save').disabled = busy || !canEdit || !dirty();
        $('activate').disabled = busy || !canActivate || filterDirty() || !(dirty() || staged || filterStaged);
        $('filter-save').disabled = busy || !canEdit || !filterRevision || !filterDirty();
        $('publish-hint').hidden = !filterDirty();
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
        [['Filtering', modes[result.summary.filterMode]],
            ['Patroontype', filtering ? result.summary.filterType.toUpperCase() : '—'],
            ['Filterdoel', filtering ? (result.summary.filterURLs ? 'HTTP-URL’s' : 'Domeinen') : '—']].forEach(([label, value]) => {
            const card = document.createElement('div'), small = document.createElement('small'), strong = document.createElement('strong');
            small.textContent = I18n.t(label); strong.textContent = I18n.t(value); card.append(small, strong); $('summary').append(card);
        });
        message(staged || filterStaged ? 'Er staat een opgeslagen concept klaar om te publiceren.' : 'Bestand geladen. Wijzigingen zijn pas actief na een geslaagde activatie.');
    }
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
