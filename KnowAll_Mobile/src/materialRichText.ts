import MarkdownIt from 'markdown-it'
import texmath from 'markdown-it-texmath'
import katex from 'katex'
import { katexOfflineCss } from './generated/katexOfflineCss.js'

export interface RichReading { raw: string; html: string; errors: number; fallback: string }
export const richTextNeeded = (text: string) => /(^|\n)\s*(#{1,6}\s|[-*+]\s|\d+\.\s|```|~~~|>|\|)|[*_`]\S|\[[^\]\n]*\]\(|\$[^\s$]|\\[([]/.test(text)
export function renderMaterialText(raw: string): RichReading {
  const result = { raw, html: '', errors: 0, fallback: '' }
  if (raw.length > 60000) return { ...result, fallback: '这段正文较长，已显示完整原始文字。' }
  const md = new MarkdownIt({ html: false, breaks: true, linkify: false })
  md.use(texmath, { engine: katex, delimiters: ['dollars', 'brackets'] })
  // A numeric currency opener must not swallow a later amount or an inline code span.
  md.inline.ruler.before('math_inline', 'material_dollar_guard', (state, silent) => {
    const start = state.pos
    if (state.src[start] !== '$' || state.src[start + 1] === '$') return false
    let end = start + 1
    while ((end = state.src.indexOf('$', end)) >= 0 && state.src[end - 1] === '\\') end++
    if (end < 0 || (!/\d/.test(state.src[end + 1] || '') && !state.src.slice(start + 1, end).includes('`'))) return false
    if (!silent) state.push('text', '', 0).content = '$'
    state.pos++
    return true
  })
  let formulas = 0
  // The plugin handles delimiters; our renderer keeps errors verbatim and bounds expansion.
  for (const name of Object.keys(md.renderer.rules).filter(name => name.startsWith('math_'))) {
    md.renderer.rules[name] = (tokens, index) => {
      const token = tokens[index]
      if (++formulas > 100 || token.content.length > 4000) throw new Error('Formula limit')
      const display = name.includes('block') || name === 'math_inline_double'
      try {
        if (/\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData)\b/.test(token.content)) throw new Error('External math commands')
        const rendered = katex.renderToString(token.content, { displayMode: display, trust: false, throwOnError: true, maxExpand: 1000, maxSize: 20, strict: 'ignore' })
        return display ? rendered : `<span class="math-inline">${rendered}</span>`
      } catch {
        result.errors++
        const opening = token.markup || token.tag || (display ? '$$' : '$')
        const closing = opening === '\\(' ? '\\)' : opening === '\\[' ? '\\]' : opening
        return `<code class="math-error">${md.utils.escapeHtml(opening)}${md.utils.escapeHtml(token.content)}${md.utils.escapeHtml(closing)}</code>`
      }
    }
  }
  md.renderer.rules.image = (tokens, index) => `<span class="image-description">图片说明：${md.utils.escapeHtml(tokens[index].content || '插图')}</span>`
  md.renderer.rules.link_open = () => '<span class="reference">'
  md.renderer.rules.link_close = () => '</span>'
  try {
    result.html = md.render(raw).replace(/<table>/g, '<div class="wide"><table>').replace(/<\/table>/g, '</table></div>')
    if (result.html.length > 1000000) throw new Error('Rendered size limit')
  } catch { result.html = ''; result.fallback = '这段内容暂时无法排版，已显示完整原始文字。' }
  return result
}
export function materialTextHtml(body: string, theme: { color: string; background: string; muted: string; border: string; fontSize: number }): string {
  // Values come from the app theme, not uploaded content. Still validate CSS values at this boundary.
  const color = (value: string) => /^(#[\da-f]{3,8}|rgba?\([\d.,\s%]+\))$/i.test(value) ? value : '#222'
  const size = Math.max(16, Math.min(26, theme.fontSize))
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src 'none'; script-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<style>${katexOfflineCss}
html,body{margin:0;padding:0;width:100%;background:${color(theme.background)};color:${color(theme.color)};font-family:system-ui,sans-serif;font-size:${size}px;line-height:1.9;overflow-wrap:anywhere}body{display:flow-root;padding-bottom:8px}*{box-sizing:border-box}h1,h2,h3,h4,h5,h6{line-height:1.4;margin:1em 0 .5em}h1{font-size:1.5em}h2{font-size:1.3em}h3{font-size:1.15em}p{margin:.5em 0 1em}ul,ol{padding-left:1.5em}blockquote{margin:1em 0;padding-left:12px;border-left:3px solid ${color(theme.border)};color:${color(theme.muted)}}pre,.wide,.katex-display{max-width:100%;overflow-x:auto}pre{padding:12px;border:1px solid ${color(theme.border)};border-radius:8px;white-space:pre;overflow-wrap:normal;font-size:.85em}code{font-family:monospace}.math-error{white-space:pre-wrap}.math-inline{display:inline-block;max-width:100%;overflow-x:auto;vertical-align:middle}.katex{font-size:1.1em}.katex-display{padding:12px 0;text-align:left}.katex-display>.katex{display:inline-block;white-space:nowrap}table{border-collapse:collapse;min-width:100%;white-space:nowrap}td,th{border:1px solid ${color(theme.border)};padding:8px 12px;text-align:left}.reference,.image-description{color:${color(theme.muted)}}
</style></head><body>${body}</body></html>`
}
