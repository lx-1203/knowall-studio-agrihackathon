import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: ['native-agent-chat.spec.ts', 'native-agent-native.spec.ts', 'chat-ui-patrol.spec.ts', '18-product-auth.spec.ts', 'quiz-analytics-lazy.spec.ts', '04-quiz.spec.ts'],
  timeout: 30000, expect: { timeout: 10000 }, workers: 1,
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-chat-2026-10-11/frontend/production-tests.json' }]],
  outputDir: 'test-results-agent-production',
  use: { baseURL: 'http://localhost:5284', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium-production', use: { ...devices['Desktop Chrome'] } }],
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5284 --strictPort', url: 'http://localhost:5284', reuseExistingServer: false, timeout: 30000 },
})
