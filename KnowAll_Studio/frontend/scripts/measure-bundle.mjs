#!/usr/bin/env node
/**
 * Bundle critical-path analyzer for KnowAll Studio frontend.
 *
 * Why this exists
 * ---------------
 * "Total JS bytes" is a misleading metric for this app: it barely changed
 * (-0.5%) between a config that loaded 285 KB gzip on first paint and one that
 * loaded 663 KB gzip. What actually matters is the *initial critical path* —
 * the entry chunk plus everything it statically imports, because that is what
 * the browser must download and evaluate before the app can render.
 *
 * This script builds that number from the emitted ESM chunks. It distinguishes
 * static imports (`from"./x.js"`) from dynamic ones (`import("./x.js")`), so
 * code-split page chunks are correctly excluded from the critical path.
 *
 * Usage
 * -----
 *   node scripts/measure-bundle.mjs [distDir] [--json]
 *
 *   # compare two builds
 *   node scripts/measure-bundle.mjs dist-a
 *   node scripts/measure-bundle.mjs dist-b
 *
 *   # regression gate: exit 1 if the entry critical path exceeds a gzip budget
 *   node scripts/measure-bundle.mjs dist --max-entry-gz=400
 *
 * Reported per directory:
 *   TOTAL          every emitted .js chunk (raw / gzip)
 *   ENTRY+static   the initial critical path: entry + static-import closure
 *   per-page       page chunk + its static closure + the entry closure,
 *                  i.e. what one page actually costs a cold visitor
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join, basename } from 'node:path'
import { gzipSync } from 'node:zlib'

const args = process.argv.slice(2)
const asJson = args.includes('--json')
const budgetArg = args.find((a) => a.startsWith('--max-entry-gz='))
const maxEntryGzKb = budgetArg ? Number(budgetArg.split('=')[1]) : null
if (budgetArg && !Number.isFinite(maxEntryGzKb)) {
  console.error(`Invalid --max-entry-gz value: ${budgetArg}`)
  process.exit(2)
}
const dists = args.filter((a) => !a.startsWith('--'))
if (dists.length === 0) dists.push('dist')

const STATIC_FROM = /from\s*"(\.\/[^"]+)"/g
const STATIC_BARE = /(?<!import\()import\s*"(\.\/[^"]+)"/g
const DYNAMIC = /import\(\s*"(\.\/[^"]+)"\s*\)/g

const matches = (text, re) => [...text.matchAll(re)].map((m) => basename(m[1]))

function analyze(dist) {
  const assetsDir = join(dist, 'assets')
  if (!existsSync(assetsDir)) {
    return { dist, error: `no assets directory at ${assetsDir}` }
  }

  const files = new Map()
  for (const fn of readdirSync(assetsDir)) {
    if (!fn.endsWith('.js')) continue
    const raw = readFileSync(join(assetsDir, fn))
    const text = raw.toString('utf8')
    const dyn = new Set(matches(text, DYNAMIC))
    const stat = new Set(
      [...matches(text, STATIC_FROM), ...matches(text, STATIC_BARE)].filter((s) => !dyn.has(s)),
    )
    files.set(fn, { raw: raw.length, gz: gzipSync(raw, { level: 6 }).length, stat, dyn })
  }

  const closure = (start) => {
    const seen = new Set()
    const queue = [start]
    while (queue.length) {
      const cur = queue.pop()
      if (seen.has(cur) || !files.has(cur)) continue
      seen.add(cur)
      for (const next of files.get(cur).stat) queue.push(next)
    }
    return seen
  }

  // The real entry is whatever index.html references, not "the biggest index-*.js".
  let entry = null
  const htmlPath = join(dist, 'index.html')
  if (existsSync(htmlPath)) {
    const m = readFileSync(htmlPath, 'utf8').match(/src="\.?\/?assets\/([^"]+\.js)"/)
    if (m) entry = m[1]
  }
  if (!entry) {
    const candidates = [...files.keys()].filter((f) => f.startsWith('index-'))
    entry = candidates.sort((a, b) => files.get(b).raw - files.get(a).raw)[0] ?? null
  }

  const sum = (set, key) => [...set].reduce((acc, f) => acc + (files.get(f)?.[key] ?? 0), 0)
  const entryClosure = entry ? closure(entry) : new Set()

  const pageLoads = {}
  const pageLoadsGz = {}
  for (const f of files.keys()) {
    if (!/^[A-Z]\w*Page-/.test(f)) continue
    const page = f.split('-')[0]
    const set = new Set([...closure(f), ...entryClosure])
    pageLoads[page] = Math.max(pageLoads[page] ?? 0, sum(set, 'raw'))
    pageLoadsGz[page] = Math.max(pageLoadsGz[page] ?? 0, sum(set, 'gz'))
  }

  return {
    dist,
    entry,
    nJs: files.size,
    totalRaw: sum(new Set(files.keys()), 'raw'),
    totalGz: sum(new Set(files.keys()), 'gz'),
    entryRaw: sum(entryClosure, 'raw'),
    entryGz: sum(entryClosure, 'gz'),
    entryChunks: [...entryClosure]
      .map((f) => ({ file: f, raw: files.get(f).raw }))
      .sort((a, b) => b.raw - a.raw),
    pageLoads: Object.fromEntries(
      Object.entries(pageLoads).sort((a, b) => b[1] - a[1]),
    ),
    pageLoadsGz,
  }
}

const results = dists.map(analyze)

if (asJson) {
  console.log(JSON.stringify(results, null, 2))
} else {
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`
  for (const r of results) {
    console.log('='.repeat(72))
    if (r.error) {
      console.log(`${r.dist}: ${r.error}`)
      continue
    }
    console.log(`dist=${r.dist}  entry=${r.entry}  jsFiles=${r.nJs}`)
    console.log(`  TOTAL         raw=${String(r.totalRaw).padStart(9)} (${kb(r.totalRaw).padStart(9)})  gz=${String(r.totalGz).padStart(8)} (${kb(r.totalGz).padStart(8)})`)
    console.log(`  ENTRY+static  raw=${String(r.entryRaw).padStart(9)} (${kb(r.entryRaw).padStart(9)})  gz=${String(r.entryGz).padStart(8)} (${kb(r.entryGz).padStart(8)})  chunks=${r.entryChunks.length}`)
    for (const c of r.entryChunks.slice(0, 8)) {
      console.log(`      ${String(c.raw).padStart(9)}  ${c.file}`)
    }
    console.log('  per-page cold load (page + entry closure), top 8:')
    for (const [page, bytes] of Object.entries(r.pageLoads).slice(0, 8)) {
      const gz = r.pageLoadsGz?.[page] ?? 0
      console.log(
        `      ${page.padEnd(26)} raw ${String(bytes).padStart(9)} (${kb(bytes).padStart(9)})   gz ${String(gz).padStart(8)} (${kb(gz).padStart(9)})`,
      )
    }
  }
}

// Optional regression gate. Rationale for the default budget lives in
// KnowAll_Studio/docs/optimization-report.md §2: the entry critical path was
// measured at ~286 KB gzip, and a rejected chunking config pushed it to 663 KB.
if (maxEntryGzKb != null) {
  let breached = false
  console.log('-'.repeat(72))
  for (const r of results) {
    if (r.error) continue
    const gzKb = r.entryGz / 1024
    const ok = gzKb <= maxEntryGzKb
    if (!ok) breached = true
    console.log(
      `${ok ? 'PASS' : 'FAIL'} ${r.dist}: entry critical path ${gzKb.toFixed(1)} KB gzip ` +
        `(budget ${maxEntryGzKb} KB, headroom ${(maxEntryGzKb - gzKb).toFixed(1)} KB)`,
    )
  }
  if (breached) process.exit(1)
}

// ---------------------------------------------------------------------------
// Per-page budgets.
//
// The entry budget above only watches the shared critical path, so the page-level
// wins from B16/B17/B18 — ~120 KB gzip removed from three pages — were documented
// in optimization-report.md §16-§18 and then **guarded by nothing**. Reverting any
// of those three `React.lazy()` calls would not have failed a single check.
//
// Budgets sit ~2.5% above the values measured on 2026-09-20 (report §24) rather
// than exactly at them: bytes drift by a few dozen across builds (see §24's
// "per-page drift" note), and a gate that trips on +3 B gets disabled by whoever
// hits it. The margin still catches what matters — losing a lazy-loading win
// costs tens of KB, not tens of bytes.
//
// Update a number deliberately (with a fresh measurement) when a page changes;
// never widen it to silence a failure you have not explained.
const PAGE_BUDGETS_KB = {
  KnowledgePage: 445, // measured 434.4 KB gzip — B16 (§16) removed react-markdown/micromark from first paint
  QuizPage: 406, // measured 395.9 KB gzip — B17 (§17) moved antd's Table/rc-table off first paint
  StudyPage: 379, // measured 369.6 KB gzip — B18 (§18) moved antd's DatePicker/rc-picker into the modal
}

if (args.includes('--page-budgets')) {
  let breached = false
  console.log('-'.repeat(72))
  for (const r of results) {
    if (r.error) continue
    for (const [page, budgetKb] of Object.entries(PAGE_BUDGETS_KB)) {
      const gz = r.pageLoadsGz?.[page]
      if (gz == null) {
        // A renamed/removed page must not silently drop its budget check.
        breached = true
        console.log(`FAIL ${r.dist}: page "${page}" has a budget but was not found in the build`)
        continue
      }
      const gzKb = gz / 1024
      const ok = gzKb <= budgetKb
      if (!ok) breached = true
      console.log(
        `${ok ? 'PASS' : 'FAIL'} ${r.dist}: ${page} cold load ${gzKb.toFixed(1)} KB gzip ` +
          `(budget ${budgetKb} KB, headroom ${(budgetKb - gzKb).toFixed(1)} KB)`,
      )
    }
  }
  if (breached) process.exit(1)
}
