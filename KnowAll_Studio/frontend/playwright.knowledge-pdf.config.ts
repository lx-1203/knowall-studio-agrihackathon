import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: 'knowledge-network-pdf.spec.ts', workers: 1,
  timeout: 30000, expect: { timeout: 6000 }, reporter: [['line']],
  outputDir: '.tmp/knowledge-pdf-r03-results',
  use: { baseURL: 'http://127.0.0.1:5397', screenshot: 'only-on-failure', video: 'off' },
  projects: [{ name: 'pdf-source-chrome', use: { ...devices['Desktop Chrome'],
    launchOptions: { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-proxy-server'] } } }],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js preview --outDir .tmp/knowledge-pdf-build-r03 --host 127.0.0.1 --port 5397 --strictPort',
    url: 'http://127.0.0.1:5397', reuseExistingServer: false, timeout: 60000,
  },
})
