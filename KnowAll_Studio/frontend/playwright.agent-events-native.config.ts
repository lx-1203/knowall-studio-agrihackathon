import { defineConfig } from '@playwright/test'
import base from './playwright.agent-events.config'
export default defineConfig({ ...base,
  testMatch: 'agent-live-native.spec.ts', timeout: 45000,
  use: { ...base.use, baseURL: 'http://localhost:5316' },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-events-2026-10-11/native-browser/browser-tests.json' }]],
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --config .tmp/agent-events-native-preview.config.ts --host 127.0.0.1 --port 5316 --strictPort', url: 'http://localhost:5316', reuseExistingServer: false, timeout: 30000 },
})
