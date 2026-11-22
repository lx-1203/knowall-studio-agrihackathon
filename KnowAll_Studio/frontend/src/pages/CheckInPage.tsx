import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Calendar, Card, Input, Space, Spin, Statistic } from 'antd'
import { CalendarOutlined, CheckCircleOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { isAxiosError } from 'axios'
import zhCN from 'antd/es/calendar/locale/zh_CN'
import 'dayjs/locale/zh-cn'
import api from '../api'
import { useAuthStore } from '../stores'
import './check-in.css'

type Entry = { date: string; note: string; checked_at: string }
type Summary = { today: string; month: string; checked_in_today: boolean; current_streak: number; longest_streak: number; total_days: number; records: Entry[] }
function errorText(error: unknown) { return isAxiosError(error) && typeof error.response?.data?.detail === 'string' ? error.response.data.detail : '打卡记录暂未确认，请刷新核对后重试。重复提交不会多记一天。' }
function toBeijingClock(value: string) { return new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(11, 16) }

export default function CheckInPage() {
  const userId = useAuthStore(state => state.user?.id)
  const [stored, setStored] = useState<{ owner: string; data: Summary } | null>(null)
  const data = stored && stored.owner === userId ? stored.data : null
  const [month, setMonth] = useState(''), [selected, setSelected] = useState(''), [note, setNote] = useState('')
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('')
  const revision = useRef(0), lock = useRef(false), owner = useRef(userId)
  owner.current = userId
  const load = useCallback(async () => {
    if (!userId || lock.current) return
    const current = ++revision.current; setLoading(true); setError('')
    try {
      const result = (await api.get<Summary>('/check-ins', { params: month ? { month } : {} })).data
      if (!result.today || !Array.isArray(result.records)) throw new Error()
      if (current === revision.current && owner.current === userId) { setStored({ owner: userId, data: result }); setSelected(old => old || result.today) }
    } catch (e) { if (current === revision.current && owner.current === userId) setError(errorText(e)) }
    finally { if (current === revision.current && owner.current === userId) setLoading(false) }
  }, [userId, month])
  useEffect(() => { setStored(null); setMonth(''); setSelected(''); setNote(''); setMessage('') }, [userId])
  useEffect(() => {
    void load()
    const focus = () => { if (document.visibilityState === 'visible') void load() }
    window.addEventListener('focus', focus); document.addEventListener('visibilitychange', focus)
    let timer: ReturnType<typeof setTimeout>
    const refresh = () => { if (lock.current) { timer = setTimeout(refresh, 1000); return }; void load(); schedule() }
    const schedule = () => { timer = setTimeout(refresh, 86400000 - ((Date.now() + 8 * 3600000) % 86400000) + 250) }
    schedule()
    return () => { revision.current++; clearTimeout(timer); window.removeEventListener('focus', focus); document.removeEventListener('visibilitychange', focus) }
  }, [load])
  const save = async () => {
    if (lock.current || !userId || data?.checked_in_today) return
    lock.current = true; setBusy(true); setError(''); setMessage(''); const current = ++revision.current
    try {
      const result = (await api.post<Summary & { already_checked_in: boolean }>('/check-ins', { note: note.trim() })).data
      if (!result.checked_in_today || !Array.isArray(result.records)) throw new Error()
      if (current === revision.current && owner.current === userId) { setStored({ owner: userId, data: result }); setMonth(result.month); setSelected(result.today); setNote(''); setMessage(result.already_checked_in ? '今天已经打过卡，记录已同步。' : '打卡成功，今天的坚持已保存。') }
    } catch (e) { if (current === revision.current && owner.current === userId) setError(errorText(e)) }
    finally { lock.current = false; if (owner.current === userId) setBusy(false) }
  }
  const records = new Map(data?.records.map(item => [item.date, item]) || [])
  const entry = records.get(selected)
  let heading = '今天的坚持，从这里开始'
  if (data?.checked_in_today) heading = '今天，也向前了一点'
  if (error) heading = '打卡状态待确认'
  const changeMonth = (offset: number) => {
    if (!data || loading || busy) return
    const next = dayjs(data.month+'-01').add(offset, 'month').format('YYYY-MM')
    setMonth(next); setSelected(next+'-01'); setMessage('')
  }
  return <main className="check-in-page">
    <header><p className="check-in-eyebrow">每天一点，留下学习的足迹</p><h1>每日打卡</h1><p>按北京时间每日记录一次，手机与电脑同步。</p></header>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => void load()} disabled={busy}>重新读取</Button>} />}
    {!data && !error && <div className="check-in-loading"><Spin tip="正在读取打卡记录" /></div>}
    {data && <>
      <div className="check-in-stats">{[[data.current_streak, '连续打卡'], [data.longest_streak, '最长连续'], [data.total_days, '累计打卡']].map(([value, title]) => <Card key={title}><Statistic title={title} value={value} suffix="天" /></Card>)}</div>
      <div className="check-in-columns">
        <Card className="check-in-today" title={<Space><CalendarOutlined />{data.today.replace(/-/g, '.')} · 北京时间</Space>}>
          <h2>{heading}</h2>
          <p>{data.checked_in_today ? '今日打卡已保存，明天再来继续。' : '完成今天的学习后，给自己留一个记录。'}</p>
          {!data.checked_in_today && <Input.TextArea aria-label="打卡心得" placeholder="今天学到了什么？（选填）" value={note} onChange={event => setNote(event.target.value)} maxLength={300} showCount rows={4} disabled={busy} />}
          <Button type="primary" size="large" icon={<CheckCircleOutlined />} block loading={busy} disabled={busy || loading || !!error || data.checked_in_today} onClick={() => void save()} style={{ marginTop: 28 }}>{data.checked_in_today ? '今日已打卡' : '完成今日打卡'}</Button>
          {!!message && <Alert type="success" message={message} showIcon style={{ marginTop: 18 }} />}
          <p className="check-in-footnote">打卡不增加做题数量或学习时长。记录仅在当天保存，不提供补签。</p>
        </Card>
        <Card title="打卡日历" loading={loading && !!month && month !== data.month}>
          <Calendar locale={zhCN} fullscreen={false} value={dayjs(selected || data.today)} disabledDate={value => value.format('YYYY-MM-DD') > data.today || value.format('YYYY-MM') !== data.month}
            headerRender={() => <div className="check-in-calendar-heading"><Button aria-label="上个月" icon={<LeftOutlined />} disabled={loading || busy || data.month === '1900-01'} onClick={() => changeMonth(-1)} /><strong>{dayjs(data.month+'-01').format('YYYY 年 M 月')}</strong><Button aria-label="下个月" icon={<RightOutlined />} disabled={loading || busy || data.month >= data.today.slice(0, 7)} onClick={() => changeMonth(1)} /><Button size="small" disabled={loading || busy} onClick={() => { setMonth(data.today.slice(0, 7)); setSelected(data.today) }}>今天</Button></div>}
            onSelect={value => setSelected(value.format('YYYY-MM-DD'))}
            cellRender={(value, info) => info.type === 'date' ? <span className="check-in-calendar-dot" aria-label={records.has(value.format('YYYY-MM-DD')) ? '已打卡' : '未打卡'}>{records.has(value.format('YYYY-MM-DD')) ? '●' : '\u00a0'}</span> : info.originNode} />
          <p className="check-in-footnote">● 已打卡 · 本月 {data.records.length} 天</p>
          <div className="check-in-record"><strong>{selected} · {entry ? '打卡记录' : '未打卡'}</strong><p>{entry ? entry.note || '这一天，你也坚持了学习。' : '这天还没有打卡记录。'}</p>{entry && <span>保存于北京时间 {toBeijingClock(entry.checked_at)}</span>}</div>
        </Card>
      </div>
    </>}
  </main>
}
