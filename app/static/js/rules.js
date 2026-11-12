'use strict';
(() => {
    const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
    function render(entry, readOnly = false) {
        if (!Number.isInteger(entry.id) || entry.id < 0) throw new TypeError('Invalid rule ID');
        const id = entry.id;
        const type = ['domain', 'regex', 'comment', 'empty'].includes(entry.type) ? entry.type : 'regex';
        const labels = { domain: 'Domein', regex: 'Regex', comment: 'Opmerking', empty: 'Leeg' };
        const t = window.I18n.t;
        const primary = type === 'comment' ? entry.content || entry.display : entry.domain_hint || entry.display || entry.content;
        const pattern = entry.domain_hint && entry.display && entry.domain_hint !== entry.display
            ? `<details class="rule-pattern"><summary>${escape(t('Technisch patroon'))}</summary><code>${escape(entry.display)}</code></details>` : '';
        const edit = type !== 'empty' ? `<button type="button" class="button button-secondary" ${readOnly ? 'aria-disabled="true"' : ''} onclick="editEntry(${id})"><span aria-hidden="true">✏️</span><span>${escape(t('Bewerk'))}</span></button>` : '';
        return `<article class="entry-item rule-card ${type}" draggable="${!readOnly}" data-entry-id="${id}"
            ondragstart="handleDragStart(event, ${id})" ondragover="handleDragOver(event)" ondragleave="handleDragLeave(event)" ondrop="handleDrop(event, ${id})" ondragend="handleDragEnd(event)">
            <span class="entry-drag-handle" title="${escape(t('Sleep om te verplaatsen'))}" aria-hidden="true">⠿</span>
            <div class="rule-body"><div class="rule-heading"><span class="entry-type ${type}">${escape(t(labels[type]))}</span><span class="rule-title">${escape(primary || t('Lege regel'))}</span></div>
            ${entry.description ? `<p class="rule-description">${escape(entry.description)}</p>` : ''}${pattern}</div>
            <div class="entry-actions" aria-label="${escape(t('Regelacties'))}">${edit}<button type="button" class="button button-danger" ${readOnly ? 'aria-disabled="true"' : ''} onclick="deleteEntry(${id})"><span aria-hidden="true">🗑️</span><span>${escape(t('Verwijder'))}</span></button></div>
        </article>`;
    }
    window.RuleView = Object.freeze({ render });
})();
