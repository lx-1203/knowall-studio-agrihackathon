import { useState } from 'react'
import { Alert, Button, Card, Empty, Segmented, Skeleton, Tag } from 'antd'
import { ExpandOutlined, ReloadOutlined, TeamOutlined, FileTextOutlined, QuestionCircleOutlined, ReadOutlined } from '@ant-design/icons'
import { count, errorText, localTime, useAdminRead, type Overview } from './adminApi'

export default function AdminOverview({ wall = false }: { wall?: boolean }) {
  const [days, setDays] = useState(7)
  const read = useAdminRead<Overview>(`/overview?days=${days}`)
  const overview = read.data
  const [fullscreenError, setFullscreenError] = useState(false)
  const fullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await document.querySelector('.admin-wall')?.requestFullscreen()
      setFullscreenError(false)
    } catch { setFullscreenError(true) }
  }
  const maxCalls = Math.max(1, ...(overview?.daily.map(day => day.calls) || []))
  const modelTotal = overview?.models.reduce((total, model) => total + model.calls, 0) || 0
  const docTotal = overview?.document_types.reduce((total, type) => total + type.count, 0) || 0
  const success = overview?.period.calls ? overview.period.successes / overview.period.calls * 100 : null
  const cards = overview ? [
    { title: '注册用户', value: overview.totals.users, note: `${count(overview.totals.active_users)} 个可用账户`, icon: <TeamOutlined /> },
    { title: '资料总量', value: overview.totals.documents, note: '全平台已保存资料', icon: <FileTextOutlined /> },
    { title: '题库题目', value: overview.totals.questions, note: '全平台已保存题目', icon: <QuestionCircleOutlined /> },
    { title: '复习记录', value: overview.totals.reviews, note: `${count(overview.totals.flashcards)} 张知识卡片`, icon: <ReadOutlined /> },
  ] : []
  return <section className={wall ? 'admin-wall' : 'admin-overview'} aria-label={wall ? '数据大屏' : '运行概览'}>
    <div className="admin-section-heading">
      <div><span className="admin-eyebrow">{wall ? 'KNOWALL · DATA OBSERVATORY' : 'PLATFORM OVERVIEW'}</span><h2>{wall ? '智识工坊 · 数据驾驶舱' : '平台运行概览'}</h2><p>全平台真实记录 · {days} 天调用趋势（UTC 自然日）{overview && ` · 更新于 ${localTime(overview.generated_at)}`}</p></div>
      <div className="admin-toolbar"><Segmented aria-label="统计时间范围" value={days} onChange={value => setDays(Number(value))} options={[{ label: '近7天', value: 7 }, { label: '近30天', value: 30 }]} /><Button icon={<ReloadOutlined />} loading={read.pending} onClick={read.reload}>刷新数据</Button>{wall && <Button icon={<ExpandOutlined />} onClick={() => void fullscreen()}>全屏显示</Button>}</div>
    </div>
    {fullscreenError && <Alert type="info" closable message="浏览器暂不支持全屏，仍可在当前页面查看大屏。" />}
    {read.error ? <Alert type="error" showIcon message="概览加载失败" description={errorText(read.error)} action={<Button onClick={read.reload}>重新加载</Button>} /> : read.pending || !overview ? <Card><Skeleton active paragraph={{ rows: 12 }} /></Card> : <>
      <div className="admin-metrics">{cards.map(card => <article className="admin-metric" key={card.title}><div className="admin-metric-label">{card.title}{card.icon}</div><strong>{count(card.value)}</strong><span>{card.note}</span></article>)}</div>
      <div className="admin-grid-main">
        <Card className="admin-trend" title="模型调用趋势" extra={<Tag color="cyan">近 {days} 天</Tag>}>
          <div className="admin-inline-stats"><div><span>调用次数</span><strong>{count(overview.period.calls)}</strong></div><div><span>调用成功率</span><strong>{success === null ? '—' : `${success.toFixed(1)}%`}</strong></div><div><span>处理量（tokens）</span><strong>{count(overview.period.tokens)}</strong></div><div><span>平均耗时</span><strong>{count(Math.round(overview.period.avg_duration_ms))}<small> ms</small></strong></div></div>
          {overview.period.calls === 0 ? <Empty description="所选时间范围内暂无调用记录" /> : <div className="admin-chart" role="img" aria-label={`近${days}天调用量，共${count(overview.period.calls)}次`}>
            <div className="admin-chart-grid"><span>{maxCalls}</span><span>{Math.round(maxCalls / 2)}</span><span>0</span></div>
            <div className="admin-bars">{overview.daily.map((day, index) => <div className="admin-bar-column" key={day.date} title={`${day.date}：${day.calls} 次调用，${day.successes} 次成功`}><div className="admin-bar-track"><div className="admin-bar-total" style={{ height: `${day.calls / maxCalls * 100}%` }} /><div className="admin-bar-success" style={{ height: `${day.successes / maxCalls * 100}%` }} /></div><span>{days <= 7 || index % 5 === 0 || index === overview.daily.length - 1 ? day.date.slice(5).replace('-', '/') : ''}</span></div>)}</div>
          </div>}
          <div className="admin-chart-legend"><span><i />全部调用</span><span><i />成功调用</span><span>悬停查看每日数据</span></div>
        </Card>
        <Card title="学习表现" className="admin-learning"><div className="admin-score" role="img" aria-label={`累计练习正确率${overview.learning.accuracy}%`} style={{ '--score': `${Math.max(0, Math.min(100, overview.learning.accuracy))}%` } as React.CSSProperties}><div><strong>{overview.learning.attempts ? `${overview.learning.accuracy.toFixed(1)}%` : '—'}</strong><span>累计练习正确率</span></div></div><div className="admin-learning-foot"><div><strong>{count(overview.learning.attempts)}</strong><span>答题记录</span></div><div><strong>{count(overview.learning.correct)}</strong><span>正确作答</span></div></div><p className="admin-hint">按全部已记录答题计算；无答题时不计算正确率。</p></Card>
      </div>
      <div className="admin-grid-bottom"><Card title="模型调用分布" extra={<span className="admin-hint">近 {days} 天</span>}>{!modelTotal ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无模型调用" /> : <div className="admin-distribution">{overview.models.map(model => <div key={model.model}><div><strong>{model.model}</strong><span>{count(model.calls)} 次 · {count(model.tokens)} tokens</span></div><div className="admin-progress-track"><i style={{ width: `${model.calls / modelTotal * 100}%` }} /></div></div>)}</div>}</Card><Card title="资料类型分布" extra={<span className="admin-hint">全部资料</span>}>{!docTotal ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无上传资料" /> : <div className="admin-distribution">{overview.document_types.map(type => <div key={type.type}><div><strong>{type.type.toUpperCase()}</strong><span>{count(type.count)} 份 · {(type.count / docTotal * 100).toFixed(1)}%</span></div><div className="admin-progress-track admin-progress-blue"><i style={{ width: `${type.count / docTotal * 100}%` }} /></div></div>)}</div>}</Card></div>
    </>}
  </section>
}
