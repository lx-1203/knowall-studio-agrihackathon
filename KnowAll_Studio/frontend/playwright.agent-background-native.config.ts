import { defineConfig } from '@playwright/test'
import base from './playwright.agent-background.config'
export default defineConfig({ ...base, testMatch: 'agent-background-native.spec.ts', timeout: 45000,
  use: { ...base.use, baseURL: 'http://localhost:5336' },
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-background-2026-10-11/native-browser/browser-tests.json' }]],
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --config .tmp/agent-background-native-preview.config.ts --host 127.0.0.1 --port 5336 --strictPort', url: 'http://localhost:5336', reuseExistingServer: false, timeout: 30000 },
})
