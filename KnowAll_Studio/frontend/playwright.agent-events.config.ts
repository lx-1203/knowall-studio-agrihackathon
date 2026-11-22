import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: ['agent-live-events.spec.ts', 'agent-history-receipts.spec.ts', 'native-agent-chat.spec.ts', 'chat-ui-patrol.spec.ts', '18-product-auth.spec.ts'],
  workers: 1, timeout: 30000, expect: { timeout: 8000 },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-events-2026-10-11/frontend/production-tests.json' }]],
  outputDir: 'test-results-agent-events',
  use: { baseURL: 'http://localhost:5296', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium-agent-events', use: { ...devices['Desktop Chrome'] } }],
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5296 --strictPort', url: 'http://localhost:5296', reuseExistingServer: false, timeout: 30000 },
})
