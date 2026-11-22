type RecordValue = Record<string, unknown>
const record = (value: unknown): RecordValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {}
const string = (value: unknown) => typeof value === 'string' ? value : ''
export interface DocumentHit { key: string; documentId: string; chunkId: string; title: string; text: string; pageRange: string }
export function resultArray(value: unknown, field = 'results'): unknown[] {
  const items = Array.isArray(value) ? value : record(value)[field]
  if (!Array.isArray(items)) throw Error('Invalid retrieval response')
  return items
}
export function normalizeDocumentHits(value: unknown): DocumentHit[] {
  return resultArray(value).map((item, index) => {
    const hit = record(item), meta = record(hit.metadata), id = string(hit.id)
    return { key: `${id}:${index}`, documentId: string(meta.doc_id), chunkId: id.startsWith('document:') ? '' : id,
      title: string(meta.filename) || '来源资料', text: string(hit.text), pageRange: string(meta.page_range) }
  })
}
export interface DiscoveryCard { id: string; front: string; back: string; hints: string }
export function normalizeCards(value: unknown): DiscoveryCard[] {
  return resultArray(value).map(item => {
    const card = record(item)
    if (typeof card.id !== 'string' || typeof card.front !== 'string' || typeof card.back !== 'string') throw Error('Invalid card response')
    return { id: card.id, front: card.front, back: card.back, hints: string(card.hints) }
  })
}
export interface DiscoveryNode { label: string; summary: string; children: DiscoveryNode[] }
export function normalizeTreeNodes(value: unknown): DiscoveryNode[] {
  if (!Array.isArray(value)) throw Error('Invalid tree nodes')
  const roots: DiscoveryNode[] = [], queue = [{ input: value, output: roots }]
  // Iterative traversal avoids a recursive call stack for deep imported trees.
  while (queue.length) {
    const { input, output } = queue.pop()!
    for (const item of input) {
      const raw = record(item), node: DiscoveryNode = { label: string(raw.label) || string(raw.title) || '未命名知识点', summary: string(raw.summary) || string(raw.explanation), children: [] }
      output.push(node)
      if (Array.isArray(raw.children)) queue.push({ input: raw.children, output: node.children })
    }
  }
  return roots
}
export function visibleTreeNodes(nodes: DiscoveryNode[], expanded: Set<string>) {
  const rows: Array<DiscoveryNode & { path: string; depth: number; indent: number }> = []
  const stack = nodes.map((node, i) => ({ node, path: String(i), depth: 0 })).reverse()
  while (stack.length) {
    const { node, path, depth } = stack.pop()!
    rows.push({ ...node, path, depth, indent: Math.min(depth, 2) * 12 })
    if (expanded.has(path)) for (let i = node.children.length - 1; i >= 0; i--) stack.push({ node: node.children[i], path: `${path}.${i}`, depth: depth + 1 })
  }
  return rows
}
