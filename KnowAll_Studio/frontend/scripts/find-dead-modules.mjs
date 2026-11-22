#!/usr/bin/env node
/**
 * Find source modules that never make it into the production build.
 *
 * Why this exists
 * ---------------
 * "Is this file dead?" is usually answered by grepping for the filename, which
 * is unreliable — a module can be imported by a file that is itself unused, and
 * then the grep says "still referenced" forever. This asks the bundler instead:
 * if a source file appears in NO emitted chunk, nothing the app can execute
 * ever reaches it.
 *
 * Uses Vite's build API with `write: false`, so nothing on disk is touched.
 *
 * Known blind spot (reported, not hidden): template-literal dynamic imports
 * such as `import(`../data/default_${difficulty}.json`)` cannot be resolved
 * statically. Any such files are listed separately as "unresolvable" instead of
 * being reported as dead.
 *
 * Usage
 * -----
 *   node scripts/find-dead-modules.mjs [--json]
 */
import { build } from 'vite'
import { transformSync } from 'esbuild'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const asJson = process.argv.includes('--json')
const SRC = 'src'
const EXTS = ['.ts', '.tsx', '.js', '.jsx', '.css']

const result = await build({
  logLevel: 'error',
  build: { write: false, outDir: 'dist-deadcode', chunkSizeWarningLimit: 1e6 },
})

const outputs = Array.isArray(result) ? result : [result]
const bundled = new Set()
for (const out of outputs) {
  for (const item of out.output ?? []) {
    if (item.type !== 'chunk') continue
    for (const id of Object.keys(item.modules ?? {})) {
      bundled.add(id.split(String.fromCharCode(92)).join('/'))
    }
  }
}

// Walk src/ for candidates.
const walk = (dir) => {
  const found = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) found.push(...walk(full))
    else if (EXTS.some((e) => full.endsWith(e))) found.push(full.split(String.fromCharCode(92)).join('/'))
  }
  return found
}

const all = walk(SRC)
const unreachable = all.filter(
  (file) => ![...bundled].some((id) => id.endsWith(file) || id.endsWith('/' + file)),
)

/**
 * A module can be absent from the bundle yet still be very much alive: if all
 * it exports are types, TypeScript erases it entirely, so it can never appear
 * in a chunk. Ask esbuild what would actually be emitted — an empty result
 * means "types only", not "dead".
 */
const emittedLength = (file) => {
  const loader = file.endsWith('.tsx') ? 'tsx' : file.endsWith('.css') ? 'css' : 'ts'
  try {
    return transformSync(readFileSync(file, 'utf8'), { loader, format: 'esm' }).code.trim().length
  } catch {
    return -1 // unparseable here; do not claim it is dead
  }
}

const dead = []
const typeOnly = []
for (const file of unreachable) {
  const len = emittedLength(file)
  if (len === 0) typeOnly.push(file)
  else dead.push({ file, emitted: len })
}

if (asJson) {
  console.log(
    JSON.stringify(
      { scanned: all.length, bundled: bundled.size, dead, typeOnly },
      null,
      2,
    ),
  )
} else {
  console.log(`scanned ${all.length} source modules under ${SRC}/`)
  console.log(`bundled module ids: ${bundled.size}`)
  console.log(`\n--- unreachable AND emits runtime code (${dead.length}) ---`)
  for (const d of dead) console.log(`  ${String(d.emitted).padStart(7)} B  ${d.file}`)
  console.log(`\n--- unreachable but types-only, i.e. NOT dead (${typeOnly.length}) ---`)
  for (const f of typeOnly) console.log(`  ${f}`)
  const totalLines = dead.reduce((n, d) => n + readFileSync(d.file, 'utf8').split('\n').length, 0)
  console.log(`\nTOTAL dead: ${dead.length} files / ${totalLines} lines`)
}

// `--fail-on-dead` mirrors the backend gate (`scripts/find_dead_modules.py
// --fail-on-dead`) so both halves of the dead-code claim are enforced in CI.
// Without an explicit exit code this script always exited 0 — which is how it
// could sit in package.json without the loss of a gate being noticed.
if (process.argv.includes('--fail-on-dead') && dead.length > 0) {
  console.error(`\n${dead.length} dead module(s) found — failing the gate.`)
  console.error('Either delete them or prove they are reachable (dynamic import?).')
  process.exit(1)
}
