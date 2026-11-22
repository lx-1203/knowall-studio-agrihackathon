import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: 'agent-isolated-preview.spec.ts', workers: 1,
  timeout: 45000, expect: { timeout: 10000 },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-durable-2026-10-11/isolated-preview-tests.json' }]],
  outputDir: 'test-results-agent-isolated',
  use: { baseURL: 'http://127.0.0.1:5288', screenshot: 'only-on-failure' },
  projects: [{ name: 'isolated-real-app', use: { ...devices['Desktop Chrome'] } }],
})
