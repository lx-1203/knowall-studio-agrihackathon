import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { pdfResources } from './scripts/pdf-resources'

// Vendor chunking: split ONLY the small, always-needed framework deps into
// their own long-lived cacheable chunks.
//
// Measured (KnowAll_Studio/docs/optimization-report.md, reproducible via
// `npm run analyze:bundle`): forcing antd and the heavy per-domain
// libs (doc/table/markdown/graph/code) into single buckets made the entry chunk
// statically import the whole 1.19 MB antd chunk, so the initial critical path
// grew 882 KB -> 2.19 MB raw (285 KB -> 663 KB gzip) and Rollup emitted
// circular-chunk warnings ("vendor -> react -> vendor"). Those buckets are
// therefore deliberately absent: Rollup's own graph keeps heavy libraries
// inside the lazy page chunks that actually use them, which is where they
// belong. react/utils are the two groups that are genuinely needed by the app
// shell on every load, so hoisting them out only improves caching.
const VENDOR_GROUPS: Record<string, RegExp> = {
  react: /^react($|\/)|^react-dom($|\/)|^react-router($|\/)|^react-router-dom($|\/)|^scheduler($|\/)/,
  // axios is deliberately NOT listed: grouping it here forced it into the
  // same chunk as zustand/dayjs, which the app shell statically imports — so
  // axios (17.4 KB gzip) rode onto the initial critical path even though only
  // the lazy api.ts chunk needs it.
  utils: /^zustand($|\/)|^dayjs($|\/)|^lodash($|\/)|^classnames($|\/)/,
}

// Extract the bare package name from a node_modules path, preserving the
// scope for scoped packages (e.g. "@ant-design/icons" not just "@ant-design").
function pkgName(id: string): string {
  const after = id.split('node_modules/')[1]
  if (!after) return ''
  const seg = after.split('/')
  if (seg[0].startsWith('@')) return seg.length > 1 ? `${seg[0]}/${seg[1]}` : seg[0]
  return seg[0]
}

export default defineConfig({
  plugins: [react(), pdfResources(), {
    name: 'dedicated-admin-entry',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url?.split('?')[0] === '/admin') {
          response.writeHead(302, { Location: '/admin/' }); response.end(); return
        }
        next()
      })
    },
  }],
  resolve: {
    alias: {
      // antd -> rc-resize-observer imports `resize-observer-polyfill`, which is
      // ~33 KB of the initial critical path. That polyfill is only needed by
      // browsers predating native ResizeObserver (Chrome 64 / FF 69 / Safari
      // 13.1), and this project's build target is Vite's default "modules"
      // (Chrome 87+ / FF 78+ / Safari 14+) — so it is dead weight for every
      // browser actually supported here. See src/shims/resize-observer.ts.
      'resize-observer-polyfill': fileURLToPath(
        new URL('./src/shims/resize-observer.ts', import.meta.url),
      ),
    },
  },
  server: {
    port: 5273,
    strictPort: true,
    watch: {
      // Historical previews and test artifacts are not application source.
      ignored: ['**/.tmp/**', '**/dist/**', '**/dist-knowledge-network/**', '**/playwright-report/**'],
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:8001',
        ws: true,
        changeOrigin: true,
      },
      // molbio 快照：dev 下转发到后端 GET /glossary_terms.kgb（FileResponse data/glossary_terms.kgb）
      '/glossary_terms.kgb': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        admin: fileURLToPath(new URL('./admin/index.html', import.meta.url)),
      },
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            const pkg = pkgName(id)
            for (const [group, pattern] of Object.entries(VENDOR_GROUPS)) {
              if (pattern.test(pkg)) return group
            }
            // No catch-all bucket. Naming one shared chunk for every other
            // node_module is what dragged all of antd into the entry's static
            // import graph; let Rollup place them instead.
          }
          return undefined
        },
      },
    },
  },
})
