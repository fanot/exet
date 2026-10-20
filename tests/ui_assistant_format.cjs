const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('api/static/assistant.js', 'utf8');
const start = source.indexOf('  function inline(');
const end = source.indexOf('  function validSources(', start);
assert(start >= 0 && end > start);
const esc = text => String(text ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const format = vm.runInNewContext(`${source.slice(start, end)}\n({cleanAssistantText, markdown})`, {esc});

const raw = `Шаг 1. Область работ\n<box gap={2}>\n<row align=start gap={3}>\n<AsyncImage query="aerial survey" aspectRatio="3:4"/>\nЧто делать\n</row>\n</box>\n\n| Модель | Количество | АКБ на аппарат |\n| --- | ---: | ---: |\n| Геоскан 201 | 2 | 3 |`;
const cleaned = format.cleanAssistantText(raw);
assert.doesNotMatch(cleaned, /<\/?(?:box|row|AsyncImage)/i);
assert.match(cleaned, /Что делать/);
const html = format.markdown(cleaned, []);
assert.match(html, /<table>/);
assert.match(html, /<th scope="col">Модель<\/th>/);
assert.match(html, /<td>Геоскан 201<\/td>/);
assert.doesNotMatch(html, /\| ---/);

assert.equal(format.cleanAssistantText('Текст <AsyncImage query="незавершено'), 'Текст');
assert.match(format.markdown('**Важно** <script>alert(1)</script> [D1]', []), /<strong>Важно<\/strong> &lt;script&gt;/);
assert.match(format.markdown('1. Первый\n2. Второй', []), /<ol><li>Первый<\/li><li>Второй<\/li><\/ol>/);
console.log('Assistant markup cleanup and table rendering passed');
