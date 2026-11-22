import { defineConfig } from '@playwright/test'
import base from './playwright.agent-shutdown.config'
export default defineConfig({ ...base, use: { ...base.use, baseURL: 'http://localhost:5339' },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-shutdown-2026-10-11/frontend/dev-tests.json' }]],
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5339 --strictPort', url: 'http://localhost:5339', reuseExistingServer: false, timeout: 30000 },
})
