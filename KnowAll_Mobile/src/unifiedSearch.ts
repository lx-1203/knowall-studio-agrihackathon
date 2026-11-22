export type SearchScope = 'mine' | 'shared'
export type SearchKind = 'all' | 'docs' | 'points' | 'cards' | 'terms'
export type SearchSource = Exclude<SearchKind, 'all'> | 'shared'
export type SearchRequest = { key: SearchSource; method: 'get' | 'post'; path: string; params?: Record<string, string | number>; body?: Record<string, string | number> }

export function normalizeSearchScope(value: unknown): SearchScope { return value === 'shared' ? 'shared' : 'mine' }
export function normalizeSearchKind(value: unknown): SearchKind {
  if (value === 'knowledge') return 'points'
  return value === 'docs' || value === 'points' || value === 'cards' || value === 'terms' ? value : 'all'
}

export function searchPlan(query: string, scope: SearchScope, kind: SearchKind, count = 10, mode = 'keyword'): SearchRequest[] {
  const q = query.trim()
  if (!q) return []
  const limit = Math.max(10, Math.min(40, Math.trunc(count) || 10))
  if (scope === 'shared') return [{ key: 'shared', method: 'get', path: '/shared-library/search', params: { q, offset: 0, limit } }]
  const docs: SearchRequest = { key: 'docs', method: 'post', path: '/search/', body: { query: q, top_k: limit, mode: 'keyword' } }
  if (mode === 'related') { docs.path = '/search/rag'; docs.body = { query: q, top_k: limit } }
  const sources: SearchRequest[] = [docs,
    { key: 'points', method: 'get', path: '/shared-library/knowledge/search', params: { q, offset: 0, limit } },
    { key: 'cards', method: 'get', path: '/flashcards/search', params: { q, top_k: limit } },
    { key: 'terms', method: 'get', path: '/glossary/terms', params: { q, page: 1, page_size: limit } },
  ]
  return kind === 'all' ? sources : sources.filter(source => source.key === kind)
}
