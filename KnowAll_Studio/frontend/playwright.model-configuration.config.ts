import base from './playwright.config'
import { defineConfig } from '@playwright/test'

export default defineConfig({
  ...base, testDir: './tests/e2e', testMatch: 'api-configuration-repair.spec.ts', workers: 1,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5286', launchOptions: { args: ['--no-proxy-server'] } },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js preview --outDir .tmp/model-connections-build --host 127.0.0.1 --port 5286 --strictPort',
    url: 'http://127.0.0.1:5286', reuseExistingServer: false, timeout: 30000,
  },
})
