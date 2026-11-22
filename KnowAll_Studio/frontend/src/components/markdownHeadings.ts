import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import { visit } from 'unist-util-visit'
import { toString } from 'mdast-util-to-string'

export interface MarkdownHeading { id: string; level: number; text: string }
const parser = unified().use(remarkParse).use(remarkGfm)

// Native source offsets identify duplicates without deriving IDs from user text.
export function headingId(prefix: string, offset: number) { return `${prefix}-heading-${offset}` }

export function parseMarkdownHeadings(content: string, prefix: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = []
  visit(parser.parse(content), 'heading', node => {
    const offset = node.position?.start.offset
    if (offset !== undefined) headings.push({ id: headingId(prefix, offset), level: node.depth, text: toString(node) })
  })
  return headings
}
