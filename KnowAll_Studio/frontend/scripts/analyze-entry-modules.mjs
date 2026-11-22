#!/usr/bin/env node
/**
 * Attribute the entry chunk's bytes to packages / source modules.
 *
 * Why this exists
 * ---------------
 * `measure-bundle.mjs` tells you *how big* the initial critical path is, but not
 * *what* is in it. Chasing a size problem without attribution leads to guessing.
 *
 * This asks Vite for the build graph in memory (`write: false`, so nothing on
 * disk is touched) and then uses Rollup's own `chunk.imports` / `chunk.modules`
 * metadata — not regexes — to:
 *   1. resolve the entry chunk and its static-import closure (the real critical path),
 *   2. aggregate `renderedLength` per npm package and per app source file.
 *
 * Usage
 * -----
 *   node scripts/analyze-entry-modules.mjs [--top=25] [--json]
 */
import { build } from 'vite'

const argv = process.argv.slice(2)
const topArg = argv.find((a) => a.startsWith('--top='))
const TOP = topArg ? Number(topArg.split('=')[1]) : 25
const rootArg = argv.find((a) => a.startsWith('--root='))
const pagesArg = argv.includes('--pages')
const asJson = argv.includes('--json')

const result = await build({
  logLevel: 'error',
  build: { write: false, outDir: 'dist-analyze', chunkSizeWarningLimit: 100000 },
})

const outputs = Array.isArray(result) ? result : [result]
const chunks = new Map()
for (const out of outputs) {
  for (const item of out.output ?? []) {
    if (item.type === 'chunk') chunks.set(item.fileName, item)
  }
}

// Root chunk: the entry by default, or a page chunk via --root=<substring>.
// Analysing a page answers "what does opening THIS page cost", which is closer
// to what a user feels than the shell's own number.
// Prefer a chunk whose basename STARTS WITH the root: `--root=QuizPage` must not
// silently resolve to InteractiveQuizPage, which merely contains the string.
const rootName = rootArg?.split('=')[1]
const allChunks = [...chunks.values()]
const entry = rootArg
  ? (allChunks.find((c) => c.fileName.split('/').pop().startsWith(`${rootName}-`)) ??
     allChunks.find((c) => c.fileName.includes(rootName)))
  : allChunks.find((c) => c.isEntry)
if (!entry) {
  console.error(`No chunk found for ${rootArg ?? 'the entry'}`)
  process.exit(2)
}

// Static-import closure == what the browser must fetch before first render.
const closureFrom = (start) => {
  const seen = new Set()
  const q = [start]
  while (q.length) {
    const n = q.pop()
    if (seen.has(n) || !chunks.has(n)) continue
    seen.add(n)
    for (const dep of chunks.get(n).imports) q.push(dep)
  }
  return seen
}

const closure = closureFrom(entry.fileName)
const entryClosure = closureFrom([...chunks.values()].find((c) => c.isEntry)?.fileName)

const pkgOf = (id) => {
  const norm = id.replace(/\\/g, '/')
  const marker = 'node_modules/'
  const idx = norm.lastIndexOf(marker)
  if (idx === -1) return null
  const seg = norm.slice(idx + marker.length).split('/')
  return seg[0].startsWith('@') ? `${seg[0]}/${seg[1]}` : seg[0]
}

const byPackage = new Map()
const byModule = []
let total = 0

for (const name of closure) {
  const chunk = chunks.get(name)
  for (const [id, mod] of Object.entries(chunk.modules ?? {})) {
    const len = mod.renderedLength ?? 0
    if (len === 0) continue
    total += len
    const pkg = pkgOf(id) ?? '(app source)'
    byPackage.set(pkg, (byPackage.get(pkg) ?? 0) + len)
    byModule.push({ id, pkg, len })
  }
}

const sortedPkgs = [...byPackage.entries()].sort((a, b) => b[1] - a[1])
byModule.sort((a, b) => b.len - a.len)

const kb = (n) => `${(n / 1024).toFixed(1)} KB`
const pct = (n) => `${((n / total) * 100).toFixed(1)}%`

if (pagesArg) {
  // One build, every page: report what each page ADDS on top of the app shell.
  // That delta is the thing a visitor to that page actually pays for, and it is
  // where a lazy-loading win would show up.
  console.log(`entry closure: ${entryClosure.size} chunks\n`)
  const rows = []
  for (const c of chunks.values()) {
    const base = c.fileName.split('/').pop()
    if (!/^[A-Z]\w*Page-/.test(base)) continue
    const page = base.split('-')[0]
    const added = new Set([...closureFrom(c.fileName)].filter((n) => !entryClosure.has(n)))
    let total = 0
    const pkgs = new Map()
    for (const n of added) {
      for (const [id, mod] of Object.entries(chunks.get(n).modules ?? {})) {
        const len = mod.renderedLength ?? 0
        if (!len) continue
        total += len
        const pkg = pkgOf(id) ?? '(app source)'
        pkgs.set(pkg, (pkgs.get(pkg) ?? 0) + len)
      }
    }
    rows.push({ page, total, added: added.size, top: [...pkgs].sort((a, b) => b[1] - a[1]).slice(0, 6) })
  }
  rows.sort((a, b) => b.total - a.total)
  for (const r of rows) {
    console.log(`${r.page.padEnd(24)} +${String(r.total).padStart(9)} (${kb(r.total).padStart(9)})  over ${String(r.added).padStart(3)} page-only chunks`)
    for (const [pkg, len] of r.top) console.log(`        ${String(len).padStart(9)}  ${pkg}`)
  }
} else if (asJson) {
  console.log(JSON.stringify({ entry: entry.fileName, closure: [...closure], total, packages: sortedPkgs, modules: byModule.slice(0, TOP) }, null, 2))
} else {
  console.log(`entry chunk : ${entry.fileName}`)
  console.log(`static closure: ${[...closure].join(', ')}`)
  console.log(`attributed bytes: ${total} (${kb(total)})`)
  console.log('\n--- bytes by package (entry closure) ---')
  for (const [pkg, len] of sortedPkgs.slice(0, TOP)) {
    console.log(`  ${String(len).padStart(9)}  ${pct(len).padStart(6)}  ${pkg}`)
  }
  console.log(`\n--- largest individual modules (top ${TOP}) ---`)
  for (const m of byModule.slice(0, TOP)) {
    const short = m.id.replace(/\\/g, '/').split('node_modules/').pop()
    console.log(`  ${String(m.len).padStart(9)}  ${pct(m.len).padStart(6)}  [${m.pkg}] ${short}`)
  }
}
