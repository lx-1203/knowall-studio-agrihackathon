import { defineConfig } from '@playwright/test'
import base from './playwright.agent-events.config'
export default defineConfig({ ...base,
  testMatch: [...base.testMatch as string[], 'agent-background-runs.spec.ts'],
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-background-2026-10-11/frontend/production-tests.json' }]],
  outputDir: 'test-results-agent-background', use: { ...base.use, baseURL: 'http://localhost:5326' },
  webServer: { command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5326 --strictPort', url: 'http://localhost:5326', reuseExistingServer: false, timeout: 30000 },
})
