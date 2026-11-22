import { defineConfig } from '@playwright/test'
import production from './playwright.agent-durable.config'
export default defineConfig({
  ...production,
  use: { ...production.use, baseURL: 'http://localhost:5277' },
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5277 --strictPort', url: 'http://localhost:5277', reuseExistingServer: false, timeout: 30000 },
})
