const asar = require('@electron/asar')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const assert = require('node:assert/strict')
const root = path.resolve(__dirname, '..')
const output = process.env.KNOWALL_DESKTOP_RESOURCES || path.join(root, 'release', 'win-unpacked', 'resources')
const archive = path.join(output, 'app.asar')
const files = asar.listPackage(archive)
assert.ok(files.length > 0)
for (const file of files) {
  assert.ok(!/(^|\/)(backend|node_modules|data)(\/|$)|(^|\/)\.env|\.(db|sqlite|pem|key)($|\.)/.test(file), `Forbidden package file: ${file}`)
}
const shellFiles = ['main.js', 'preload.js', 'cloud-main.js', 'cloud-gateway.js', 'connection.html', 'connection.js', 'connection-preload.js', 'assets/icon.png']
const sha = content => crypto.createHash('sha256').update(content).digest('hex')
for (const file of shellFiles) assert.equal(sha(asar.extractFile(archive, file)), sha(fs.readFileSync(path.join(root, file))), `Stale packaged shell: ${file}`)
const frontendRoot = process.env.KNOWALL_FRONTEND_DIST || path.resolve(root, '..', 'frontend', 'dist')
const quiz = fs.readFileSync(path.join(frontendRoot, 'molbio-quiz.html'), 'utf8')
assert.ok(quiz.includes('const ME_API = "/api/v1/auth/me";'), 'Quiz authentication must use the configured same-origin service')
assert.ok(quiz.includes('const API = "/api/v1/quiz/progress";'), 'Quiz progress must use the configured same-origin service')
assert.ok(!quiz.includes('http://127.0.0.1:8001'), 'Quiz contains a hardcoded development backend')
assert.match(quiz, /b\.className\s*=\s*"syncbadge "\s*\+\s*type;/, 'Quiz sync status class must use its type argument')
assert.match(quiz, /b\.textContent\s*=\s*text;/, 'Quiz sync status must display its message argument')
assert.equal(sha(quiz), sha(fs.readFileSync(path.resolve(root, '..', 'frontend', 'public', 'molbio-quiz.html'))), 'Quiz dist asset differs from current public source')
const frontendFiles = []
function inspect(directory, relative = '') {
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(relative, item.name)
    if (item.isDirectory()) inspect(path.join(directory, item.name), file)
    else {
      assert.ok(!/(^|[\/\\])(data|\.env)([\/\\]|$)|\.(db|sqlite|map|pem|key)($|\.)/.test(file), `Forbidden frontend file: ${file}`)
      assert.equal(sha(fs.readFileSync(path.join(directory, item.name))), sha(fs.readFileSync(path.join(frontendRoot, file))), `Stale frontend asset: ${file}`)
      frontendFiles.push(file)
    }
  }
}
inspect(path.join(output, 'frontend'))
assert.ok(frontendFiles.includes('index.html'))
const sourceFiles = []
function collect(directory, relative = '') { for (const item of fs.readdirSync(directory, { withFileTypes: true })) { const file = path.join(relative, item.name); if (item.isDirectory()) collect(path.join(directory, item.name), file); else if (!file.endsWith('.map')) sourceFiles.push(file) } }
collect(frontendRoot)
assert.deepEqual(frontendFiles.sort(), sourceFiles.sort(), 'Packaged UI asset set differs from latest dist')
console.log(`PASS package audit: ${shellFiles.length} shell files match current source; ${frontendFiles.length} frontend files match current dist; no backend, secrets or runtime data`)
