import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { createHash } from 'node:crypto'
import type { Plugin } from 'vite'

/** Vite's public directory ships PDF.js originals for both dev and packaged clients. */
export function pdfResources(): Plugin {
  return {
    name: 'knowall-pdfjs-resources',
    configResolved(config) {
      const require = createRequire(import.meta.url)
      const source = dirname(require.resolve('pdfjs-dist/package.json'))
      const version = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8')).version
      if (!config.publicDir) throw new Error('PDF.js resources require a public directory')
      const target = join(config.publicDir, 'pdfjs')
      mkdirSync(target, { recursive: true })
      const files: Record<string, string> = {}
      for (const folder of ['cmaps', 'standard_fonts', 'wasm']) {
        cpSync(join(source, folder), join(target, folder), { recursive: true })
        for (const name of readdirSync(join(source, folder))) {
          files[`${folder}/${name}`] = createHash('sha256').update(readFileSync(join(source, folder, name))).digest('hex')
        }
      }
      cpSync(join(source, 'LICENSE'), join(target, 'LICENSE'))
      const manifestPath = join(target, 'SOURCE.json')
      const manifest = JSON.stringify({
        package: 'pdfjs-dist', version, license: 'Apache-2.0 with original per-resource notices',
        origin: 'Installed fixed lock package; original resource files copied without edits', files,
      }, null, 2)
      // Avoid rewriting an identical manifest on every dev/preview startup.
      if (!existsSync(manifestPath) || readFileSync(manifestPath, 'utf8') !== manifest) {
        writeFileSync(manifestPath, manifest)
      }
    },
  }
}
