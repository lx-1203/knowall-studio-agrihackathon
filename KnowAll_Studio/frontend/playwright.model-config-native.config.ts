import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e', testMatch: 'model-configuration-native.spec.ts',
  timeout: 60000, expect: { timeout: 10000 }, workers: 1,
  outputDir: '.tmp/model-config-native-results',
  use: { baseURL: 'http://127.0.0.1:5287', screenshot: 'only-on-failure',
    launchOptions: { args: ['--no-proxy-server'] } },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js preview --outDir .tmp/model-connections-build --host 127.0.0.1 --port 5287 --strictPort',
    url: 'http://127.0.0.1:5287', reuseExistingServer: false, timeout: 30000,
  },
})
