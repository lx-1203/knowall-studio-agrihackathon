import type { MaterialCard, MaterialSection } from './materialLearning'

export interface MindmapBranch { key: string; title: string; notes: string; source: string[]; cards: MaterialCard[] }
export function mindmapBranches(sections: MaterialSection[], chunks: { id: string; text: string }[]): MindmapBranch[] {
  const sources = new Set(chunks.map(chunk => chunk.id))
  if (sections.length) return sections.map((section, index) => ({
    key: `${section.id}:${index}`, title: section.title || `章节 ${index + 1}`, notes: section.notes,
    source: section.chunk_ids.filter(id => sources.has(id)), cards: section.flashcards,
  }))
  return chunks.map((chunk, index) => ({ key: `source:${chunk.id}:${index}`,
    title: chunk.text.match(/^#{1,6}\s+(.+)$/m)?.[1] || chunk.text.split('\n').find(line => line.trim())?.trim().slice(0, 64) || `阅读片段 ${index + 1}`,
    notes: chunk.text, source: [chunk.id], cards: [],
  }))
}
export function filterMindmap(branches: MindmapBranch[], query: string): MindmapBranch[] {
  const term = query.trim().toLocaleLowerCase()
  if (!term) return branches
  return branches.flatMap(branch => {
    if (`${branch.title}\n${branch.notes}`.toLocaleLowerCase().includes(term)) return [branch]
    const cards = branch.cards.filter(card => `${card.front}\n${card.back}`.toLocaleLowerCase().includes(term))
    return cards.length ? [{ ...branch, cards }] : []
  })
}
