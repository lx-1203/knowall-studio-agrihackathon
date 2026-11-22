// KnowAll_Studio/frontend/src/components/GlossaryContext.tsx
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { GlossaryItem } from '../types'

/** 从文本中高亮匹配到的术语：返回 HTML 字符串（含 span.glossary-term，data-term 存规范名）。 */
export function highlightTerms(text: string, terms: GlossaryItem[]): string {
  const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  if (!terms.length || !text) return escapeHtml(text)
  // 组装所有可点击词条（规范名 + 别名），按长度降序，避免短词先匹配吃掉长词
  const allNames: Array<{ name: string; term: string; subject?: string; acronym?: boolean }> = []
  const seen = new Set<string>()
  terms.forEach(t => {
    const candidates = [t.term, ...(t.aliases || [])].filter(Boolean)
    candidates.forEach(name => {
      const key = name.toLowerCase()
      if (seen.has(key) || name.length < 2) return
      // 纯 ASCII 字母缩写（全大写，如 PCR/DNA）：大小写敏感 + 词边界匹配，防误伤小写英文词
      const isUpperAcronym = /^[A-Z][A-Z0-9]*$/.test(name) && name.length >= 2
      seen.add(key)
      allNames.push({ name, term: t.term, subject: t.subject, acronym: isUpperAcronym })
    })
  })
  allNames.sort((a, b) => b.name.length - a.name.length)

  let result = escapeHtml(text)
  // 用占位符避免多次替换互相干扰（ 为私人使用区字符，不会被转义/剥离）
  const placeholders: string[] = []
  allNames.forEach(({ name, term, acronym }) => {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    // 全大写缩写用词边界正则（\b），避免在 longer 单词内部匹配
    const re = acronym ? new RegExp(`\\b${escaped}\\b`, 'g') : new RegExp(escaped, 'g')
    result = result.replace(re, match => {
      const ph = `${placeholders.length}`
      placeholders.push(match)
      return ph
    })
  })
  // 还原
  placeholders.forEach((name, i) => {
    const safeTerm = name.replace(/"/g, '&quot;')
    result = result.replace(`${i}`, `<span class="glossary-term" data-term="${safeTerm}">${name}</span>`)
  })
  return result
}

interface GlossaryContextValue {
  terms: GlossaryItem[]
  loading: boolean
  /** 按规范名查条目（兼容别名） */
  findTerm: (name: string) => GlossaryItem | undefined
  refresh: () => Promise<void>
}

const GlossaryContext = createContext<GlossaryContextValue>({
  terms: [], loading: false, findTerm: () => undefined, refresh: async () => {},
})

export function GlossaryProvider({ children }: { children: ReactNode }) {
  const [terms, setTerms] = useState<GlossaryItem[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = async () => {
    try {
      setLoading(true)
      // Imported on demand rather than at module scope: a static import here
      // pulled axios (17.4 KB gzip) onto the app shell's initial critical path
      // for every visitor, to fetch a glossary that highlights content they may
      // never open. See KnowAll_Studio/docs/optimization-report.md §11.
      const { getGlossaryTerms } = await import('../api')
      // 后端单页上限 page_size=200，翻页拉取全量；词库不可用时无感降级（不显示高亮）
      const all: GlossaryItem[] = []
      let page = 1
      for (;;) {
        const data = await getGlossaryTerms({ page, page_size: 200 })
        const items: GlossaryItem[] = data.items || []
        all.push(...items)
        if (!items.length || all.length >= data.total || page >= 20) break
        page += 1
      }
      setTerms(all)
    } catch {
      setTerms([])  // 无感降级：词库不可用时不显示高亮
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const findTerm = (name: string) => {
    const exact = terms.find(t => t.term === name)
    if (exact) return exact
    const lower = name.toLowerCase()
    return terms.find(t => (t.aliases || []).some(a => a.toLowerCase() === lower))
  }

  const value = useMemo(() => ({ terms, loading, findTerm, refresh }), [terms, loading])
  return <GlossaryContext.Provider value={value}>{children}</GlossaryContext.Provider>
}

export function useGlossary() {
  return useContext(GlossaryContext)
}
