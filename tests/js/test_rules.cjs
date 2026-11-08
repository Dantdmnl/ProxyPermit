const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
function renderer(locale) {
    const messages = JSON.parse(fs.readFileSync(path.join(root, 'app/i18n/catalogs', locale + '.json'), 'utf8'));
    const context = { window: {}, document: { getElementById: () => ({ textContent: JSON.stringify({ locale, messages }) }) } };
    vm.createContext(context);
    for (const name of ['i18n.js', 'rules.js']) vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js', name), 'utf8'), context);
    return context.window.RuleView.render;
}
for (const locale of ['nl', 'en']) {
    const render = renderer(locale);
    const html = render({ id: 1, type: 'regex', domain_hint: '*.example.com', display: '^example\\.com$', description: 'User <script>data</script>' });
    assert.ok(html.includes('class="rule-body"'));
    assert.ok(html.indexOf('class="entry-actions"') > html.indexOf('class="rule-body"'));
    assert.ok(html.includes('*.example.com'));
    assert.ok(html.includes('User &lt;script&gt;data&lt;/script&gt;'));
    assert.ok(html.includes('aria-hidden="true">✏️'));
    assert.ok(html.includes('aria-hidden="true">🗑️'));
    assert.ok(html.includes(locale === 'en' ? '>Edit</span>' : '>Bewerk</span>'));
    assert.ok(html.includes(locale === 'en' ? 'Technical pattern' : 'Technisch patroon'));
    assert.ok(!html.includes('Oorspronkelijk:'));
    assert.throws(() => render({ id: '1);alert(1)', type: 'regex' }), /Invalid rule ID/);
}
console.log('Rule layout, localization and escaping checks passed.');
