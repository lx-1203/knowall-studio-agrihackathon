import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Alert, Button, Collapse, Spin, Tag } from 'antd'
import { loadMaterialCatalog } from '../materialCatalog'
import { libraryReturn } from '../materialProgress'
import { builtinBiology, kindLabel, materialPath, type Material } from '../materials'

export default function RelatedMaterials({ documentId, subject }: { documentId: string; subject?: string }) {
  const location = useLocation()
  const [items, setItems] = useState<Material[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let cancelled = false
    setItems([]); setError(false)
    if (!subject) return
    setLoading(true)
    loadMaterialCatalog(() => cancelled).then(result => {
      if (!cancelled) setItems([...result, builtinBiology].filter(item => item.id !== documentId && item.subject === subject))
    }).catch(() => { if (!cancelled) setError(true) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [documentId, subject, attempt])
  if (!subject) return null
  return <section className="material-related" aria-label="同科目资料">
    <Collapse ghost size="small" items={[{ key: 'related', label: '同科目资料', children: <>
      {loading ? <Spin size="small" /> : error ? <Alert type="warning" message="相关资料暂时未能加载" action={<Button size="small" onClick={() => setAttempt(value => value + 1)}>重试</Button>} /> : items.length ? <ul>{items.map(item => <li key={item.id}><Tag>{kindLabel(item.material_kind)}</Tag><Link aria-label={`打开相关资料 ${item.filename}`} to={materialPath(item.id)} state={{ returnTo: libraryReturn(location.state) }}>{item.filename}</Link></li>)}</ul> : <p className="material-muted">还没有其他{subject}资料，上传教材或题库后可在这里切换。</p>}
      <p className="material-muted">按科目关联；每份资料的原文、笔记和生成内容独立保留。</p>
    </> }]} />
  </section>
}
