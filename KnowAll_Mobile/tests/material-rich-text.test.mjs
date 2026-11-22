import { test } from 'node:test'
import assert from 'node:assert/strict'
const rich = await import('../src/materialRichText.ts').catch(() => ({}))
const render = text => { assert.equal(typeof rich.renderMaterialText, 'function', 'safe material renderer must exist'); return rich.renderMaterialText(text) }

test('headings, lists, tables and code are readable without altering their source', () => {
  const source = '# 细胞\n\n**重点**\n\n- 核膜\n- DNA\n\n| 项目 | 含义 |\n| --- | --- |\n| A | B |\n\n```tex\n$\\frac{1}{2}$\n```'
  const result = render(source)
  assert.equal(result.raw, source); assert.match(result.html, /<h1>细胞<\/h1>/)
  assert.match(result.html, /<strong>重点/); assert.match(result.html, /<table>/); assert.match(result.html, /<code[^>]*>\$\\frac/)
})
test('nested fractions, matrix and bracket delimiters use actual KaTeX layouts', () => {
  const result = render('比例 $\\frac{1}{\\sqrt{x^2+1}}$\n\n\\[\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}\\]\n\n括号 \\(x_1^2\\)')
  assert.equal(result.errors, 0); assert.match(result.html, /katex/); assert.match(result.html, /mfrac/); assert.match(result.html, /mtable/)
})
test('currency and escaped delimiters are preserved and code does not become a formula', () => {
  const result = render('价格 $20 和 $30。转义 \\$5。`$x$`')
  assert.equal(result.errors, 0); assert.doesNotMatch(result.html, /class="katex/); assert.match(result.html, /\$20 和 \$30/)
})
test('invalid formula preserves the source instead of deleting commands or failing the document', () => {
  const result = render('之前 $\\unknown{<script>}$ 之后')
  assert.equal(result.errors, 1); assert.match(result.html, /\\unknown/); assert.match(result.html, /&lt;script&gt;/); assert.doesNotMatch(result.html, /<script>/)
})
test('uploaded HTML, external images and navigable links are inert', () => {
  const result = render('<script>alert(1)</script>\n\n![说明](https://tracking.example/a.png)\n\n[外部](https://example.com)\n\n$\\href{javascript:alert(1)}{X}$')
  assert.doesNotMatch(result.html, /<script|<img|<a\b|href=["']javascript:/); assert.match(result.html, /&lt;script&gt;/); assert.match(result.html, /图片说明：说明/)
  assert.equal(result.errors, 1); assert.match(result.html, /math-error/)
})
test('source and math limits return the complete raw text for safe fallback', () => {
  for (const source of ['x'.repeat(60001), Array(102).fill('$x$').join(' '), '$' + 'x'.repeat(4001) + '$']) {
    const result = render(source); assert.equal(result.raw, source); assert.ok(result.fallback); assert.equal(result.html, '')
  }
})
test('offline HTML embeds fonts, blocks resource requests and honors theme and font size', () => {
  render('# hi')
  assert.equal(typeof rich.materialTextHtml, 'function')
  const html = rich.materialTextHtml('<p>safe</p>', { color: '#fff', background: '#123456', muted: '#abc', border: '#789', fontSize: 22 })
  assert.match(html, /data:font\/woff2;base64/); assert.match(html, /default-src 'none'/); assert.match(html, /font-size:22px/); assert.match(html, /color:#fff/)
  assert.doesNotMatch(html, /src=["']https?:|url\(["']?fonts\//)
})
test('failed bracket formulas preserve their matching closing delimiter and inline double dollars are displayed', () => {
  assert.match(render('之前 \\(\\unknown{x}\\) 之后').html, /\\\(\\unknown\{x\}\\\)/)
  assert.match(render('之前 $$\\frac{1}{2}$$ 之后').html, /katex-display/)
})
test('format detection includes fenced code, inline code, emphasis and links, not ordinary paragraphs', () => {
  assert.equal(typeof rich.richTextNeeded, 'function')
  for (const source of ['~~~text\ncode\n~~~', '_重点_', '*重点*', String.fromCharCode(96)+'code'+String.fromCharCode(96), '[出处](https://example.com)']) assert.equal(rich.richTextNeeded(source), true, source)
  assert.equal(rich.richTextNeeded('普通中文资料，直接阅读。'), false)
})
