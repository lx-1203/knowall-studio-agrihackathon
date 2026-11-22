import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Isolate this review from another chat's running browser tests.
export default defineConfig({
  ...base,
  use: { ...base.use, baseURL: 'http://localhost:5286', video: 'off' },
  projects: [{ name: 'chromium', use: { ...base.projects?.[0]?.use, channel: 'chrome' } }],
  webServer: { command: 'node ./node_modules/vite/bin/vite.js --port 5286 --strictPort', url: 'http://localhost:5286', reuseExistingServer: false, timeout: 120000 },
})
