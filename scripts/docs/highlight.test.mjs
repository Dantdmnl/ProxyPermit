import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { parse } from 'parse5';
import { highlighter, highlightDocument } from './highlight.mjs';
const engine = await highlighter();
after(() => engine.dispose());
const nodes = node => [node, ...(node.childNodes || []).flatMap(nodes)];
const text = node => node.nodeName === '#text' ? node.value : (node.childNodes || []).map(text).join('');

test('shell commands get VS Code tokens without changing copy text or anchors', () => {
    const html = '<div class="language-bash highlight"><pre><code id="copy"><span id="line-1">sudo bash install.sh --plan\n</span><span id="line-2">echo &quot;$HOME&quot; &amp;&amp; echo &lt;example&gt;\n</span></code></pre></div>';
    const result = highlightDocument(html, engine);
    const all = nodes(parse(result.html));
    assert.equal(result.count, 1);
    assert.equal(text(all.find(node => node.tagName === 'code')), 'sudo bash install.sh --plan\necho "$HOME" && echo <example>\n');
    assert.ok(all.some(node => node.attrs?.some(item => item.name === 'id' && item.value === 'copy')));
    assert.ok(all.some(node => node.attrs?.some(item => item.name === 'id' && item.value === 'line-2')));
    assert.ok(new Set(all.flatMap(node => node.attrs?.filter(item => item.name === 'style').map(item => item.value) || [])).size >= 3);
    assert.equal(highlightDocument(result.html, engine).count, 0);
});
test('plaintext blocks stay unhighlighted', () => {
    assert.equal(highlightDocument('<pre><code>arbitrary &lt;text&gt;</code></pre>', engine).count, 0);
});
