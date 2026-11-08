'use strict';
(() => {
    const element = document.getElementById('locale-data');
    const data = element ? JSON.parse(element.textContent) : { locale: 'nl', messages: {} };
    const t = text => {
        if (typeof text !== 'string') return text;
        const direct = data.messages[text];
        if (direct !== undefined && direct !== text) return direct;
        const normalized = text.replace(/\s+/g, ' ').trim();
        return data.messages[normalized] || direct || text;
    };
    const escape = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    // Only static template fragments pass here. Interpolated rule data is kept verbatim.
    function html(strings, ...values) {
        return strings.map((part, index) => {
            const translated = part.replace(/(^|>)([^<>]+)(?=<|$)/g, (match, prefix, content) => {
                const trimmed = content.trim();
                const translatedText = t(trimmed);
                return prefix + (trimmed ? content.replace(trimmed, translatedText === trimmed ? trimmed : escape(translatedText)) : content);
            }).replace(/(title|placeholder|aria-label)="([^"]+)"/g, (match, attr, value) => t(value) === value ? match : `${attr}="${escape(t(value))}"`);
            return translated + (index < values.length ? values[index] : '');
        }).join('');
    }
    function msg(strings, ...values) {
        const key = strings.map((part, index) => part + (index < values.length ? `{${index}}` : '')).join('');
        return t(key).replace(/\{(\d+)\}/g, (match, index) => String(values[Number(index)]));
    }
    window.I18n = Object.freeze({ t, html, msg, fragment: text => html([text]), locale: data.locale, tag: data.locale === 'en' ? 'en-GB' : 'nl-NL' });
})();
