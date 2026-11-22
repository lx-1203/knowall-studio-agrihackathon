import { useState, useEffect } from 'react'
import { Spin, Empty, Tabs, Table } from 'antd'
import type { TableColumnsType } from 'antd'
import * as XLSX from 'xlsx'

interface XlsxPreviewProps {
  fileUrl: string
  fileName?: string
  fileType?: string
  maxHeight?: string
}

interface SheetRow { key: number; cells: string[] }
interface PreviewSheet { name: string; data: SheetRow[]; columns: TableColumnsType<SheetRow> }

export default function XlsxPreview({ fileUrl, fileName, fileType, maxHeight = '50vh' }: XlsxPreviewProps) {
  const type = (fileType || fileName?.split('.').pop() || '').toLowerCase()
  const [sheets, setSheets] = useState<PreviewSheet[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeSheet, setActiveSheet] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    setLoading(true)
    setError('')
    setSheets([])
    setActiveSheet('')
    // The caller already authorizes the raw file and supplies its Blob URL.
    fetch(fileUrl, { signal: controller.signal })
      .then(r => {
        if (r.status === 204) throw new Error('原始文件暂不可用，可能已被删除')
        if (!r.ok) throw new Error('加载失败')
        return r.arrayBuffer()
      })
      .then(async buf => {
        if (cancelled) return
        if (type === 'xls') {
          // Native legacy encoding support is loaded only for old XLS files.
          const codepage = await import('xlsx/dist/cpexcel.full.mjs')
          if (cancelled) return
          XLSX.set_cptable(codepage)
        }
        const wb = XLSX.read(buf, type === 'csv'
          ? { type: 'array', raw: true, codepage: 65001 }
          : { type: 'array' })
        const parsed = wb.SheetNames.map(name => {
          const ws = wb.Sheets[name]
          const json = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, raw: false, defval: '' })
          if (!json.length) return { name, data: [], columns: [] }
          const headers = json[0]
          // Display headings are never data keys: duplicates and reserved names stay independent.
          const rows = json.slice(1).map((cells, key) => ({ key, cells }))
          const columns = headers.map((h, idx) => ({
            title: h === '' ? `列${idx + 1}` : h,
            dataIndex: ['cells', idx],
            key: idx,
            ellipsis: true,
            width: Math.max(100, Math.min(200, (h?.length || 4) * 16)),
          }))
          return { name, data: rows, columns }
        })
        if (cancelled) return
        setSheets(parsed)
        if (parsed.length > 0) setActiveSheet(parsed[0].name)
        setLoading(false)
      })
      .catch(err => { if (!cancelled) { setError(err.message); setLoading(false) } })
    return () => { cancelled = true; controller.abort() }
  }, [fileUrl, type])

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
  if (error) return <Empty description={`加载失败: ${error}`} />
  if (!sheets.length) return <Empty description="无数据" />

  return (
    <Tabs
      activeKey={activeSheet}
      onChange={setActiveSheet}
      size="small"
      items={sheets.map(s => ({
        key: s.name,
        label: s.name,
        children: (
          <Table
            dataSource={s.data}
            columns={s.columns}
            rowKey="key"
            size="small"
            pagination={s.data.length > 50 ? { pageSize: 50 } : false}
            scroll={{ x: 'max-content', y: maxHeight }}
            bordered
          />
        ),
      }))}
    />
  )
}
