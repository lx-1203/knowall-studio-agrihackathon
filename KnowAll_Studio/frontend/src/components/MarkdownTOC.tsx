import { useMemo } from 'react'
import { Tree } from 'antd'
import type { DataNode } from 'antd/es/tree'
import type { MarkdownHeading } from './markdownHeadings'

interface Props {
  headings: MarkdownHeading[]
  selectedId?: string
  onSelect: (id: string) => void
}

export default function MarkdownTOC({ headings, selectedId, onSelect }: Props) {
  const treeData = useMemo(() => buildTree(headings), [headings])
  if (treeData.length === 0) return <div style={{ color: '#999', fontSize: 13 }}>无目录结构</div>
  return <Tree key={headings.map(h => h.id).join(',')}
    treeData={treeData} selectedKeys={selectedId ? [selectedId] : []}
    onSelect={(_, info) => onSelect(String(info.node.key))}
    defaultExpandAll showLine={{ showLeafIcon: false }} style={{ fontSize: 13 }} />
}

// Preserve the existing Tree adapter; parsing belongs to the native Markdown AST.
function buildTree(headings: MarkdownHeading[]): DataNode[] {
  const root: DataNode[] = []
  const stack: { node: DataNode; level: number }[] = []
  for (const h of headings) {
    const node: DataNode = { key: h.id, title: h.text }
    while (stack.length && stack[stack.length - 1].level >= h.level) stack.pop()
    if (!stack.length) root.push(node)
    else {
      const parent = stack[stack.length - 1].node
      if (!parent.children) parent.children = []
      parent.children.push(node)
    }
    stack.push({ node, level: h.level })
  }
  return root
}
