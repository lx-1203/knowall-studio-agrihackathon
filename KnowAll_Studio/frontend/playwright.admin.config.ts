import { defineConfig, devices } from '@playwright/test'

const production = process.env.ADMIN_PRODUCTION === '1'

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'admin-console.spec.ts',
  timeout: 45000,
  expect: { timeout: 8000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: production ? 'test-results-admin-production' : 'test-results-admin',
  reporter: [['list'], ['json', { outputFile: production ? '../../docs/admin-console/production-results.json' : '../../docs/admin-console/playwright-results.json' }]],
  use: { baseURL: 'http://127.0.0.1:5287', timezoneId: 'Asia/Shanghai', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium-admin', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: production
      ? 'node node_modules/vite/bin/vite.js preview --outDir ../../docs/admin-console/build-current --host 127.0.0.1 --port 5287 --strictPort'
      : `node --input-type=module -e "import('vite').then(async ({createServer}) => { const server = await createServer({optimizeDeps:{entries:['admin/index.html']},server:{host:'127.0.0.1',port:5287,strictPort:true,watch:{ignored:['**/dist*/**','**/test-results*/**','**/.tmp/**','**/playwright-report/**']}}}); await server.listen() })"`,
    url: 'http://127.0.0.1:5287', reuseExistingServer: false, timeout: 60000,
  },
})
