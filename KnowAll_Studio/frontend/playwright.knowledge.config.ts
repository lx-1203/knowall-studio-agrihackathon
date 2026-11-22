import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Use the installed Chrome for this task's local evidence; CI retains base config.
export default defineConfig({ ...base, use: { ...base.use, baseURL: 'http://127.0.0.1:5284', video: 'off' }, reporter: [['line']], outputDir: '.tmp/knowledge-test-results',
  webServer: { ...base.webServer, command: 'node ./node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5284 --strictPort', url: 'http://127.0.0.1:5284' },
  projects: [{ name: 'chrome-local', use: { ...base.projects![0].use,
    launchOptions: { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-proxy-server'] } } }],
})
