import type { ComponentProps } from 'react'
import ReactMarkdown, { type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-async-light'
import vscDarkPlus from 'react-syntax-highlighter/dist/esm/styles/prism/vsc-dark-plus'
import oneLight from 'react-syntax-highlighter/dist/esm/styles/prism/one-light'
import { useTheme } from './ThemeProvider'
import { codeLanguages } from './previews/codeLanguages'
import 'katex/dist/katex.min.css'
import './previews/markdown-preview.css'

function MarkdownCodeBlock({ children, node, ...rest }: ComponentProps<'pre'> & ExtraProps) {
  const { isDark } = useTheme()
  const code = node?.children[0]
  // Math is transformed by rehype-katex before components are evaluated.
  if (code?.type !== 'element' || code.tagName !== 'code' || code.children.some(child => child.type !== 'text')) {
    return <pre {...rest}>{children}</pre>
  }
  const classes = String(code.properties.className || '')
  const name = /language-([\w-]+)/.exec(classes)?.[1]?.toLowerCase() || 'text'
  const language = codeLanguages[name] || name
  const source = code.children.map(child => child.type === 'text' ? child.value : '').join('')
  return <pre {...rest} className="markdown-code-block"><SyntaxHighlighter language={language}
    style={isDark ? vscDarkPlus : oneLight} PreTag="div" customStyle={{ margin: 0, padding: 16, fontSize: 13 }}>
    {source}
  </SyntaxHighlighter></pre>
}
function ImageDescription({ alt }: ComponentProps<'img'>) { return <span>{alt || '图片'}</span> }
// Native target attributes use the existing desktop window-open handler;
// invalid URLs already removed by ReactMarkdown remain inert anchors.
function LearningLink({ node: _node, ...props }: ComponentProps<'a'> & ExtraProps) {
  return <a {...props} target={props.href ? '_blank' : undefined} rel={props.href ? 'noopener noreferrer' : undefined} />
}
const markdownComponents = { pre: MarkdownCodeBlock }
const learningComponents = { ...markdownComponents, img: ImageDescription, a: LearningLink }

/** Shared native parser; learning content does not automatically fetch embedded images. */
export default function MarkdownContent({ text, learning = false }: { text: string; learning?: boolean }) {
  return <div className="markdown-content">
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { trust: false, maxExpand: 1000 }]]}
      components={learning ? learningComponents : markdownComponents}>{text}</ReactMarkdown>
  </div>
}
