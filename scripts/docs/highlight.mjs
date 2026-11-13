/** Build-time VS Code highlighting. Generated pages need no highlighter runtime. */
import { readFile, writeFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, parseFragment, serialize } from 'parse5';
import { createHighlighter } from 'shiki';

const languages = { bash: 'shellscript', sh: 'shellscript', shell: 'shellscript', powershell: 'powershell', dotenv: 'dotenv' };
const labels = { bash: 'Bash', sh: 'Shell', shell: 'Shell', powershell: 'PowerShell', dotenv: '.env' };
const attribute = (node, name) => node.attrs?.find(item => item.name === name)?.value;
function setAttribute(node, name, value) {
    const existing = node.attrs.find(item => item.name === name);
    if (existing) existing.value = value;
    else node.attrs.push({ name, value });
}
function descendants(node) {
    return [node, ...(node.childNodes || []).flatMap(descendants)];
}
function content(node) {
    return node.nodeName === '#text' ? node.value : (node.childNodes || []).map(content).join('');
}
export async function highlighter() {
    return createHighlighter({ themes: ['light-plus', 'dark-plus'], langs: [...new Set(Object.values(languages))] });
}
export function highlightDocument(html, engine) {
    const document = parse(html);
    let count = 0;
    for (const pre of descendants(document).filter(node => node.tagName === 'pre')) {
        const code = pre.childNodes?.find(node => node.tagName === 'code');
        if (!code || attribute(pre, 'data-highlighted') === 'shiki') continue;
        const wrapper = pre.parentNode;
        const classes = [attribute(wrapper, 'class'), attribute(pre, 'class'), attribute(code, 'class')].join(' ');
        const language = classes.match(/\blanguage-([\w+-]+)/)?.[1];
        if (!languages[language]) continue;
        // Keep the existing code element/copy target and each original line anchor.
        const anchors = descendants(code).map(node => attribute(node, 'id')).filter(Boolean);
        const source = content(code);
        const fragment = parseFragment(engine.codeToHtml(source, {
            lang: languages[language], themes: { light: 'light-plus', dark: 'dark-plus' }
        }));
        const painted = fragment.childNodes.find(node => node.tagName === 'pre');
        const inner = painted.childNodes.find(node => node.tagName === 'code');
        const lines = inner.childNodes.filter(node => attribute(node, 'class') === 'line');
        lines.forEach((line, index) => { if (anchors[index]) setAttribute(line, 'id', anchors[index]); });
        code.childNodes = inner.childNodes;
        for (const child of code.childNodes) child.parentNode = code;
        // Preserve original trailing newlines exactly: the copy button reads textContent.
        if (content(code) !== source) throw new Error('Highlighting changed command text');
        setAttribute(pre, 'class', [attribute(pre, 'class'), 'pp-code-shiki'].filter(Boolean).join(' '));
        setAttribute(pre, 'style', attribute(painted, 'style'));
        setAttribute(pre, 'data-highlighted', 'shiki');
        setAttribute(pre, 'data-language', labels[language]);
        count++;
    }
    return { html: serialize(document), count };
}
async function build(directory) {
    const engine = await highlighter();
    let total = 0;
    try {
        async function visit(folder) {
            for (const item of await readdir(folder, { withFileTypes: true })) {
                const filename = path.join(folder, item.name);
                if (item.isDirectory()) await visit(filename);
                else if (item.isFile() && item.name.endsWith('.html')) {
                    const result = highlightDocument(await readFile(filename, 'utf8'), engine);
                    if (result.count) { await writeFile(filename, result.html, 'utf8'); total += result.count; }
                }
            }
        }
        await visit(directory);
        console.log(`VS Code highlighting: ${total} code blocks rendered with Light+ / Dark+.`);
    } finally { engine.dispose(); }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    await build(path.resolve(process.argv[2] || 'site'));
}
