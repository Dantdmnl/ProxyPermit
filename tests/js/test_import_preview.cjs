const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const manager = fs.readFileSync(path.join(root, 'app/static/js/manager.js'), 'utf8');
function declaration(name) {
    const match = manager.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
    assert.ok(match, `Missing ${name}`);
    return match[0];
}
for (const locale of ['nl', 'en']) {
    const messages = JSON.parse(fs.readFileSync(path.join(root, 'app/i18n/catalogs', locale + '.json'), 'utf8'));
    const input = { value: 'new.example\n# Eigen tekst &#10; <img src=x>' };
    const preview = { style: {}, innerHTML: '' };
    const mode = { value: 'append' };
    const nodes = { importContent: input, importPreview: preview, importMode: mode, 'locale-data': { textContent: JSON.stringify({ locale, messages }) } };
    const context = { window: {}, entries: [{ type: 'domain', content: 'existing.example', original: 'existing.example' }], _importPreviewExpanded: false,
        document: { getElementById: id => nodes[id], createElement: () => ({ textContent: '', get innerHTML() {
            return String(this.textContent).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        } }) } };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js/i18n.js'), 'utf8'), context);
    context.I18n = context.window.I18n;
    for (const name of ['parseWhitelistLine', 'escapeHtml', 'previewImportContent']) vm.runInContext(declaration(name), context);
    const original = input.value;
    context.previewImportContent();
    assert.ok(preview.innerHTML.includes(locale === 'en' ? 'Rules: 2' : 'Regels: 2'));
    assert.ok(preview.innerHTML.includes(locale === 'en' ? ', comments: 1' : ', opmerkingen: 1'));
    assert.ok(preview.innerHTML.includes(locale === 'en' ? 'Add (new): 2' : 'Toevoegen (nieuw): 2'));
    assert.ok(preview.innerHTML.includes('Eigen tekst &amp;#10; &lt;img src=x&gt;'));
    assert.equal(input.value, original);
    mode.value = 'replace';
    context.previewImportContent();
    assert.ok(preview.innerHTML.includes(locale === 'en' ? 'Add: 2, remove: 1' : 'Toevoegen: 2, verwijderen: 1'));
    mode.value = 'append';
    input.value = Array.from({ length: 10 }, (_, index) => `host${index}.example`).join('\n');
    context.previewImportContent();
    assert.ok(preview.innerHTML.includes(locale === 'en' ? 'Show all (2 more)' : 'Toon alles (2 meer)'));
}
console.log('Import preview localization, counts and input preservation checks passed.');
