import { useMemo, useState } from 'react'
import { Button, Input } from 'antd'
import { DownOutlined, RightOutlined, ApartmentOutlined } from '@ant-design/icons'
import LearningContent from './LearningContent'
import type { SourceChunk } from '../materials'
import './MaterialMindMap.css'

interface Section { id: string; title: string; notes: string; chunk_ids: string[]; flashcards: { id: string; front: string; back: string }[] }
export default function MaterialMindMap({ title, sections, chunks, activeId, onSource }: {
  title: string; sections: Section[]; chunks: SourceChunk[]; activeId?: string; onSource: (ids: string[]) => void
}) {
  const [query, setQuery] = useState('')
  const [closed, setClosed] = useState<string[]>([])
  const [revealed, setRevealed] = useState<string[]>([])
  const branches = useMemo(() => {
    const sourceIds = new Set(chunks.map(chunk => chunk.id))
    return sections.length ? sections.map((section, index) => ({ ...section, key: `${section.id}:${index}`, source: (section.chunk_ids || []).filter(id => sourceIds.has(id)), flashcards: section.flashcards || [] })) : chunks.map((chunk, index) => ({
      key: `source:${chunk.id}:${index}`, title: chunk.text.match(/^#{1,6}\s+(.+)$/m)?.[1] || chunk.text.split('\n').find(line => line.trim())?.trim().slice(0, 64) || `阅读片段 ${index + 1}`,
      notes: chunk.text, source: [chunk.id], flashcards: [],
    }))
  }, [sections, chunks])
  const term = query.trim().toLocaleLowerCase()
  const visible = branches.flatMap(branch => {
    if (!term || `${branch.title}\n${branch.notes}`.toLocaleLowerCase().includes(term)) return [branch]
    const cards = branch.flashcards.filter(card => `${card.front}\n${card.back}`.toLocaleLowerCase().includes(term))
    return cards.length ? [{ ...branch, flashcards: cards }] : []
  })
  return <section className="material-mindmap" aria-label="当前资料导图">
    <div className="material-map-root"><ApartmentOutlined /><strong>{title}</strong></div>
    <p className="material-muted">{sections.length ? '章节 → 知识卡 · 点击分支展开' : '原文结构 · 无需生成即可浏览'} · {branches.length} 个分支</p>
    <Input aria-label="搜索导图" placeholder="搜索章节、讲解和知识卡" allowClear value={query} onChange={event => setQuery(event.target.value)} />
    <div className="material-map-actions"><Button size="small" onClick={() => { setQuery(''); setClosed([]) }}>全部展开</Button><Button size="small" onClick={() => { setQuery(''); setClosed(branches.map(branch => branch.key)) }}>全部收起</Button></div>
    {!visible.length && <div className="material-map-empty"><p>{branches.length ? '没有匹配的分支' : '资料解析完成后显示导图'}</p>{!!query && <Button onClick={() => setQuery('')}>清空搜索</Button>}</div>}
    <div className="material-map-tree">{visible.map((branch, index) => {
      const open = !!term || !closed.includes(branch.key)
      const regionId = `map-branch-${index}`
      return <div className="material-map-branch" key={branch.key}>
        <button className="material-map-heading" aria-label={`${open ? '收起' : '展开'}分支 ${branch.title}`} aria-expanded={open} aria-controls={regionId} disabled={!!term} onClick={() => setClosed(values => values.includes(branch.key) ? values.filter(value => value !== branch.key) : [...values, branch.key])}>
          {open ? <DownOutlined /> : <RightOutlined />}<span><strong>{branch.title}</strong><small>{branch.flashcards.length} 张知识卡{activeId && branch.source.includes(activeId) ? ' · 正在阅读' : ''}</small></span>
        </button>
        {open && <div id={regionId} className="material-map-detail">
          <div className="material-map-notes"><LearningContent text={branch.notes || '本章讲解尚未生成'} /></div>
          {branch.flashcards.map((card, position) => {
            const key = `${branch.key}:${card.id}:${position}`
            return <div className="material-map-card" key={key}><LearningContent text={card.front} />{revealed.includes(key) && <LearningContent text={card.back} />}<Button size="small" onClick={() => setRevealed(values => values.includes(key) ? values.filter(value => value !== key) : [...values, key])}>{revealed.includes(key) ? '收起卡片答案' : '查看卡片答案'}</Button></div>
          })}
          <Button size="small" disabled={!branch.source.length} onClick={() => onSource(branch.source)}>阅读分支原文</Button>
          {!branch.source.length && <p className="material-muted">来源已变化，暂不可定位</p>}
        </div>}
      </div>
    })}</div>
  </section>
}
