declare module 'markdown-it-texmath' {
  import type MarkdownIt from 'markdown-it'
  const plugin: (md: MarkdownIt, options: { engine: object; delimiters: string[] }) => void
  export default plugin
}
