import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', testMatch: ['knowledge-network.spec.ts', '34-knowledge-collections.spec.ts', '02-upload.spec.ts', '19-document-learning-flow.spec.ts'], timeout: 30000,
  expect: { timeout: 6000 }, workers: 1, reporter: [['line']], outputDir: '.tmp/knowledge-network-results',
  use: { baseURL: 'http://127.0.0.1:5296', screenshot: 'only-on-failure', video: 'off' },
  projects: [{ name: 'knowledge-network-chrome', use: { ...devices['Desktop Chrome'], launchOptions: { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-proxy-server'] } } }],
  webServer: { command: process.env.KNOWLEDGE_NETWORK_BUILT === '1'
    ? 'node node_modules/vite/bin/vite.js preview --outDir dist-knowledge-network --host 127.0.0.1 --port 5296 --strictPort'
    : `node --input-type=module -e "import { createServer } from 'vite'; import { realpathSync } from 'node:fs'; const server = await createServer({ server: { host: '127.0.0.1', port: 5296, strictPort: true, fs: { allow: [process.cwd(), realpathSync('node_modules')] } } }); await server.listen();"`,
    url: 'http://127.0.0.1:5296', reuseExistingServer: false, timeout: 120000 },
})
