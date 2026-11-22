import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: ['agent-history-receipts.spec.ts', 'native-agent-chat.spec.ts', 'native-agent-native.spec.ts', 'agent-durable-native.spec.ts', 'chat-ui-patrol.spec.ts', '18-product-auth.spec.ts'],
  timeout: 30000, expect: { timeout: 10000 }, workers: 1,
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-durable-2026-10-11/frontend/production-tests.json' }]],
  outputDir: 'test-results-agent-durable',
  use: { baseURL: 'http://localhost:5286', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium-durable', use: { ...devices['Desktop Chrome'] } }],
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5286 --strictPort', url: 'http://localhost:5286', reuseExistingServer: false, timeout: 30000 },
})
