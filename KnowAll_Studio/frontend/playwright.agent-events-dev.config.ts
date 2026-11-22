import { defineConfig } from '@playwright/test'
import base from './playwright.agent-events.config'
export default defineConfig({ ...base,
  use: { ...base.use, baseURL: 'http://localhost:5297' },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-events-2026-10-11/frontend/dev-tests.json' }]],
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5297 --strictPort', url: 'http://localhost:5297', reuseExistingServer: false, timeout: 30000 },
})
