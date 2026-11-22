import { defineConfig } from '@playwright/test'
import base from './playwright.agent-background.config'
export default defineConfig({ ...base,
  testMatch: [...base.testMatch as string[], 'agent-run-inspection.spec.ts'],
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-shutdown-2026-10-11/frontend/production-tests.json' }]],
  outputDir: 'test-results-agent-shutdown', use: { ...base.use, baseURL: 'http://localhost:5338' },
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5338 --strictPort', url: 'http://localhost:5338', reuseExistingServer: false, timeout: 30000 },
})
