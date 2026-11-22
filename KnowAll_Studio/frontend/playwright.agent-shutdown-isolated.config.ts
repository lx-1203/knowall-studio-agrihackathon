import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: 'agent-shutdown-isolated.spec.ts', workers: 1,
  timeout: 45000, expect: { timeout: 10000 },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-shutdown-2026-10-11/isolated-preview-tests.json' }]],
  outputDir: 'test-results-agent-shutdown-isolated',
  use: { baseURL: 'http://127.0.0.1:5291', screenshot: 'only-on-failure' },
  projects: [{ name: 'isolated-real-app', use: { ...devices['Desktop Chrome'] } }],
})
