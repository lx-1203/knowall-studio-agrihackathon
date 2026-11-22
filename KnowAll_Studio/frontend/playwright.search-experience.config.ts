import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: ['30-search-experience.spec.ts', '25-search-citation-flow.spec.ts', '29-search-recovery-flow.spec.ts'],
  timeout: 30000,
  expect: { timeout: 7000 },
  workers: 2,
  reporter: [['list']],
  outputDir: 'test-results-search-experience',
  use: { baseURL: 'http://localhost:5286', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], channel: 'chrome' } }],
  webServer: {
    command: `node ./node_modules/vite/bin/vite.js ${process.env.SEARCH_EXPERIENCE_PREVIEW === '1' ? 'preview ' : ''}--port 5286 --strictPort`,
    url: 'http://localhost:5286', reuseExistingServer: false, timeout: 120000,
  },
})
