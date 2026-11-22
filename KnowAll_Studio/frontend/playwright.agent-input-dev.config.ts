import { defineConfig } from '@playwright/test'
import base from './playwright.agent-shutdown.config'
export default defineConfig({ ...base, testMatch: ['agent-input-checkpoints.spec.ts'],
  reporter: [['list'], ['json', { outputFile: '../docs/open-source-integration/native-agent-input-checkpoint-2026-10-12/frontend/dev-tests.json' }]],
  outputDir: 'test-results-agent-input', use: { ...base.use, baseURL: 'http://localhost:5341' },
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5341 --strictPort', url: 'http://localhost:5341', reuseExistingServer: false, timeout: 30000 },
})
