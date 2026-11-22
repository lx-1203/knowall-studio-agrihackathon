import { defineConfig } from '@playwright/test'
import base from './playwright.agent-background.config'
export default defineConfig({ ...base, use: { ...base.use, baseURL: 'http://localhost:5327' },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-background-2026-10-11/frontend/dev-tests.json' }]],
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5327 --strictPort', url: 'http://localhost:5327', reuseExistingServer: false, timeout: 30000 },
})
