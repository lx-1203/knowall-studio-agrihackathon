// Existing file-extension adapter, shared with Markdown fence aliases.
// Tokenizing, grammar loading and rendering remain native upstream behavior.
export const codeLanguages: Record<string, string> = {
  py: 'python', js: 'javascript', ts: 'typescript', jsx: 'jsx', tsx: 'tsx',
  java: 'java', cpp: 'cpp', c: 'c', h: 'c', go: 'go', rs: 'rust',
  sql: 'sql', yaml: 'yaml', yml: 'yaml', json: 'json', xml: 'markup', css: 'css',
  html: 'markup', html_code: 'markup', markdown: 'markdown', md: 'markdown',
}
