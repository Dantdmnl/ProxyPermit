// Browser-independent regression checks for static text vs interpolated rule data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const messages = JSON.parse(fs.readFileSync(path.join(root, 'app/i18n/catalogs/en.json'), 'utf8'));
const context = { window: {}, document: { getElementById: () => ({ textContent: JSON.stringify({ locale: 'en', messages }) }) } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js/i18n.js'), 'utf8'), context);
const i18n = context.window.I18n;
assert.equal(i18n.t('Niet geladen'), 'Not loaded');
assert.equal(i18n.t('customer.example.nl'), 'customer.example.nl');
assert.equal(i18n.msg(['', ' van ', ' entries gevonden'], 2, 8), '2 of 8 rules found');
assert.equal(i18n.html(['<p>Niet geladen</p><code>', '</code>'], 'Niet geladen'), '<p>Not loaded</p><code>Niet geladen</code>');
assert.equal(i18n.fragment('<button title="Inloggen">Inloggen</button>'), '<button title="Sign in">Sign in</button>');
assert.equal(i18n.t('📥 Exporteren'), '📥 Export');
assert.equal(i18n.t('🔄 Handmatige sync'), '🔄 Manual sync');
console.log('Locale JavaScript checks passed.');
