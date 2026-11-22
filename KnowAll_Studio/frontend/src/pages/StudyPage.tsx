import { useState, useEffect, useRef, lazy, Suspense } from 'react'
import { Card, Button, Modal, Input, Progress, List, Checkbox, Tag, Empty, App, Popconfirm, Table, Space } from 'antd'
import { PlusOutlined, DeleteOutlined, ClockCircleOutlined, CheckCircleOutlined, BellOutlined, RobotOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import { useAppStore, useAuthStore } from '../stores'
import { useGlossary, highlightTerms } from '../components/GlossaryContext'
import type { DatePickerProps } from 'antd'
import { authHeaderValue } from '../authToken'
import StudySchedule from '../components/StudySchedule'

// DatePicker pulls in rc-picker + the whole dayjs locale machinery (~205 KB of
// renderedLength, the single biggest item on this page). It is used in exactly
// one place: inside the "New Study Plan" modal. Importing it from the antd
// barrel put all of that on the page's initial critical path even though most
// visits never open that modal. Loaded on demand instead.
//
// The `as React.ComponentType<DatePickerProps>` cast is load-bearing: a dynamic
// import loses the component's prop types, which silently turned the onChange
// argument into `{}` and broke `d.toISOString()` at type-check time.
// See KnowAll_Studio/docs/optimization-report.md §18.
const DatePicker = lazy(() => import('antd/es/date-picker')) as React.ComponentType<DatePickerProps>

const API = '/api/v1/study'

// Keep this page's lightweight fetch path; an uncertain write is never retried.
async function request(url: string, method = 'GET', body?: object, signal?: AbortSignal) {
  const session = useAuthStore.getState()
  const authorization = authHeaderValue()
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: authorization },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && !signal?.aborted && session === useAuthStore.getState() && authorization === authHeaderValue()) session.logout()
    throw Object.assign(new Error('学习请求失败'), { status: response.status, detail: data?.detail })
  }
  return { data }
}

const api = {
  get: (url: string, signal?: AbortSignal) => request(url, 'GET', undefined, signal),
  post: (url: string, body?: object) => request(url, 'POST', body),
  put: (url: string) => request(url, 'PUT'),
  delete: (url: string) => request(url, 'DELETE'),
}

async function readPlan(url: string, controller: AbortController) {
  const timeout = window.setTimeout(() => controller.abort(), 15_000)
  try {
    const result = await api.get(url, controller.signal)
    if (controller.signal.aborted) throw new Error('读取计划已取消或超时')
    return result
  } finally { window.clearTimeout(timeout) }
}

export default function StudyPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { terms } = useGlossary()
  const [plans, setPlans] = useState<any[]>([])
  const [selectedPlan, setSelectedPlan] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [planError, setPlanError] = useState('')
  const [detailError, setDetailError] = useState('')
  const planRead = useRef<AbortController | null>(null)
  const detailRead = useRef<AbortController | null>(null)
  const viewMounted = useRef(false)
  const viewEpoch = useRef(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newEndDate, setNewEndDate] = useState<string | null>(null)
  const [newGoals, setNewGoals] = useState('')
  const [reminders, setReminders] = useState<any[]>([])
  const [aiGenerating, setAiGenerating] = useState(false)
  const [creating, setCreating] = useState(false)
  const [reminderError, setReminderError] = useState('')
  const [reminderLoading, setReminderLoading] = useState(false)
  const [dismissingReminder, setDismissingReminder] = useState<string | null>(null)
  const reminderRead = useRef<AbortController | null>(null)
  const reminderVersion = useRef(0)
  const reminderMounted = useRef(false)
  const session = useAuthStore(state => state)
  const selection = useRef(0)
  const selectedId = useRef<string | null>(null)
  const pending = useRef(new Set<string>())
  const { selectedDoc } = useAppStore()

  const currentContext = () => {
    const epoch = viewEpoch.current
    const authorization = authHeaderValue()
    return () => viewMounted.current && epoch === viewEpoch.current && authorization === authHeaderValue()
  }

  const fetchPlans = async () => {
    if (!viewMounted.current) return
    planRead.current?.abort()
    const controller = new AbortController()
    planRead.current = controller
    const context = currentContext()
    const current = () => context() && planRead.current === controller
    setLoading(true)
    try {
      const { data } = await readPlan(API + '/plans', controller)
      if (!current()) return
      setPlans(Array.isArray(data) ? data : [])
      setPlanError('')
    } catch { if (current()) setPlanError('加载计划失败，请刷新重试') }
    finally { if (current()) { setLoading(false); planRead.current = null } }
  }

  const fetchReminders = async () => {
    const authorization = authHeaderValue()
    const scope = `reminder:${viewEpoch.current}:${authorization}:`
    if (!reminderMounted.current || [...pending.current].some(key => key.startsWith(scope))) return
    reminderRead.current?.abort()
    const controller = new AbortController()
    reminderRead.current = controller
    const version = ++reminderVersion.current
    let timedOut = false
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort() }, 15_000)
    const current = () => reminderMounted.current && (!controller.signal.aborted || timedOut) &&
      version === reminderVersion.current && authorization === authHeaderValue()
    setReminderLoading(true)
    try {
      const { data } = await api.get(API + '/reminders/due', controller.signal)
      if (timedOut) throw new Error('读取提醒超时')
      if (!current()) return
      setReminders(Array.isArray(data?.reminders) ? data.reminders : [])
      setReminderError('')
    } catch { if (current()) setReminderError('加载提醒失败') }
    finally {
      window.clearTimeout(timeout)
      if (current()) { setReminderLoading(false); reminderRead.current = null }
    }
  }

  const fetchPlanDetail = async (planId: string) => {
    if (!viewMounted.current) return
    detailRead.current?.abort()
    const controller = new AbortController()
    detailRead.current = controller
    const context = currentContext()
    const version = ++selection.current
    const current = () => context() && version === selection.current && detailRead.current === controller
    if (selectedId.current !== planId) setSelectedPlan(null)
    selectedId.current = planId
    setDetailError('')
    try {
      const { data } = await readPlan(API + `/plans/${planId}`, controller)
      if (!current()) return
      setSelectedPlan(data)
    } catch (error: any) {
      if (!current()) return
      if (error.status === 404) {
        message.warning('该计划已不存在，正在刷新列表')
        selectedId.current = null
        setSelectedPlan(null)
        fetchPlans()
        return
      }
      setDetailError('加载计划详情失败，请重试')
    } finally { if (current()) detailRead.current = null }
  }

  useEffect(() => {
    viewMounted.current = true
    viewEpoch.current++
    selection.current++; selectedId.current = null
    setPlans([]); setSelectedPlan(null); setPlanError(''); setDetailError('')
    setCreateOpen(false); setNewName(''); setNewDesc(''); setNewEndDate(null); setNewGoals('')
    setCreating(false); setAiGenerating(false)
    void fetchPlans()
    reminderMounted.current = true
    setReminders([]); setReminderError(''); setReminderLoading(false); setDismissingReminder(null)
    void fetchReminders()
    const refresh = () => { if (document.visibilityState === 'visible') void fetchReminders() }
    const timer = window.setInterval(() => {
      if (!reminderRead.current) refresh()
    }, 30_000)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      viewMounted.current = false
      viewEpoch.current++; selection.current++
      planRead.current?.abort(); planRead.current = null
      detailRead.current?.abort(); detailRead.current = null
      reminderMounted.current = false
      reminderVersion.current++
      reminderRead.current?.abort(); reminderRead.current = null
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [session])

  const write = async (key: string, failure: string, action: (current: () => boolean) => Promise<void>) => {
    const scopedKey = `plan:${authHeaderValue()}:${viewEpoch.current}:${key}`
    const current = currentContext()
    if (!current() || pending.current.has(scopedKey)) return
    pending.current.add(scopedKey)
    try { await action(current) } catch { if (current()) message.error(failure) }
    finally { pending.current.delete(scopedKey) }
  }

  const handleCreate = async () => {
    if (!newName.trim()) { message.warning('请输入计划名称'); return }
    const goals = newGoals.split('\n').map(t => t.trim()).filter(Boolean).map(title => ({ title, priority: 'medium' }))
    await write('create-plan', 'Failed to create plan', async current => {
      setCreating(true)
      try {
        await api.post(API + '/plans', { name: newName.trim(), description: newDesc, target_end_date: newEndDate, goals })
        if (!current()) return
        message.success('Plan created'); setCreateOpen(false); setNewName(''); setNewDesc(''); setNewEndDate(null); setNewGoals('')
        await fetchPlans()
      } finally { if (current()) setCreating(false) }
    })
  }

  const handleAIGenerate = async () => {
    if (!selectedDoc) { message.warning('请先在资料导入页面选择文档'); return }
    await write('generate-plan', 'AI生成失败', async current => {
      setAiGenerating(true)
      try {
        const { data } = await api.post(API + '/generate-plan', { document_id: selectedDoc, plan_duration_weeks: 4 })
        if (!current()) return
        message.success(`AI已生成学习计划: ${data.name} (${data.goal_count} 个目标)`)
        setCreateOpen(false)
        await fetchPlans()
      } catch (error: any) { if (current()) message.error(typeof error.detail === 'string' ? error.detail : 'AI生成失败') }
      finally { if (current()) setAiGenerating(false) }
    })
  }

  const handleDeletePlan = async (id: string) => {
    await write(`delete-plan:${id}`, '删除计划失败', async current => {
      await api.delete(API + `/plans/${id}`)
      if (!current()) return
      message.success('Plan deleted')
      if (selectedId.current === id) { selection.current++; selectedId.current = null; setSelectedPlan(null) }
      await fetchPlans()
    })
  }

  const handleToggleGoal = async (goalId: string) => {
    const planId = selectedId.current
    await write(`goal:${goalId}`, '更新目标失败', async current => {
      await api.put(API + `/goals/${goalId}/toggle`)
      if (!current()) return
      if (planId && selectedId.current === planId) await fetchPlanDetail(planId)
      if (current()) await fetchPlans()
    })
  }

  const handleAddGoal = async () => {
    if (!selectedPlan) return
    const title = prompt('Goal title:')
    if (!title) return
    const planId = selectedPlan.id
    await write(`add-goal:${planId}`, '添加目标失败', async current => {
      await api.post(API + '/goals', { plan_id: planId, title: title.trim() })
      if (!current()) return
      if (selectedId.current === planId) await fetchPlanDetail(planId)
      if (current()) await fetchPlans()
    })
  }

  const handleDeleteGoal = async (goalId: string) => {
    const planId = selectedId.current
    await write(`goal:${goalId}`, '删除目标失败', async current => {
      await api.delete(API + `/goals/${goalId}`)
      if (!current()) return
      if (planId && selectedId.current === planId) await fetchPlanDetail(planId)
      if (current()) await fetchPlans()
    })
  }

  const handleMarkRead = async (rId: string) => {
    const authorization = authHeaderValue()
    const scope = `reminder:${viewEpoch.current}:${authorization}:`
    const key = scope + rId
    if ([...pending.current].some(value => value.startsWith(scope))) return
    pending.current.add(key)
    setDismissingReminder(rId)
    reminderRead.current?.abort(); reminderRead.current = null
    const version = ++reminderVersion.current
    const current = () => reminderMounted.current && version === reminderVersion.current && authorization === authHeaderValue()
    setReminderLoading(false)
    let saved = false
    try {
      await api.post(API + `/reminders/${rId}/read`)
      saved = true
    } catch { if (current()) message.error('更新提醒失败') }
    finally { pending.current.delete(key); if (current()) setDismissingReminder(null) }
    if (saved && current()) await fetchReminders()
  }

  const priorityColor = (p: string) => p === 'high' ? 'red' : p === 'medium' ? 'orange' : 'green'

  return (
    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
      {/* Left: Plan list */}
      <Card title="Study Plans" extra={<Space>
        <Button size="small" loading={loading} onClick={() => void fetchPlans()}>刷新计划</Button>
        <Button icon={<RobotOutlined />} loading={aiGenerating} onClick={handleAIGenerate} disabled={!selectedDoc}>AI 生成计划</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>New Plan</Button>
      </Space>}
        style={{ flex: '1 1 300px' }} loading={loading}>
        {planError && <p role="alert">{planError}</p>}
        {plans.length === 0 && !planError && <Empty description="No study plans yet" />}
        {(plans.length > 0 || !planError) && <Table
          dataSource={plans}
          rowKey="id"
          locale={{ emptyText: 'No study plans yet' }}
          columns={[
            {
              title: '名称',
              dataIndex: 'name',
              render: (name: string, record: any) => (
                <span>{name} <Tag color={record.status === 'completed' ? 'green' : record.status === 'paused' ? 'default' : 'blue'}>{record.status}</Tag></span>
              ),
            },
            {
              title: '进度',
              dataIndex: 'progress',
              width: 200,
              render: (v: number) => <Progress percent={v} size="small" />,
            },
            {
              title: '操作',
              width: 80,
              render: (_: any, record: any) => (
                <Popconfirm title="Delete?" onConfirm={() => handleDeletePlan(record.id)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              ),
            },
          ]}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 个计划` }}
          onRow={(record) => ({
            onClick: () => fetchPlanDetail(record.id),
            style: { cursor: 'pointer', background: selectedPlan?.id === record.id ? '#e6f4ff' : undefined },
          })}
          size="middle"
        />}
      </Card>

      {/* Middle: Plan detail & goals */}
      <Card title={selectedPlan ? selectedPlan.name : 'Plan Detail'} style={{ flex: '2 1 400px' }}
        extra={selectedPlan && <Button size="small" icon={<PlusOutlined />} onClick={handleAddGoal}>Add Goal</Button>}>
        {detailError && <div><p role="alert">{detailError}</p><Button size="small" onClick={() => { if (selectedId.current) void fetchPlanDetail(selectedId.current) }}>重试详情</Button></div>}
        {!selectedPlan && !detailError && <Empty description="Select a plan to view details" />}
        {selectedPlan && (
          <div>
            <p>{selectedPlan.description}</p>
            {selectedPlan.target_end_date && <p><ClockCircleOutlined /> Target: {selectedPlan.target_end_date}</p>}
            <Progress percent={selectedPlan.progress} />
            <StudySchedule key={selectedPlan.id} plan={selectedPlan} />
            <List style={{ marginTop: 16 }} dataSource={selectedPlan.goals || []} renderItem={(g: any) => (
              <List.Item actions={[
                <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteGoal(g.id)} />,
              ]}>
                <Checkbox checked={g.completed} onChange={() => handleToggleGoal(g.id)} />
                <span
                  style={{ marginLeft: 8, textDecoration: g.completed ? 'line-through' : undefined, cursor: 'pointer' }}
                  dangerouslySetInnerHTML={{ __html: highlightTerms(g.title, terms) }}
                  onClick={(e) => {
                    const t = (e.target as HTMLElement).closest('.glossary-term') as HTMLElement | null
                    if (t) navigate(`/glossary/${encodeURIComponent(t.dataset.term || '')}`)
                  }}
                />
                <Tag color={priorityColor(g.priority)} style={{ marginLeft: 8 }}>{g.priority}</Tag>
                {g.due_date && <Tag icon={<ClockCircleOutlined />}>{g.due_date}</Tag>}
              </List.Item>
            )} />
          </div>
        )}
      </Card>

      {/* Right: Reminders */}
      <Card title={<span><BellOutlined /> Reminders ({reminders.length})</span>}
        extra={<Button size="small" loading={reminderLoading} onClick={() => void fetchReminders()}>刷新提醒</Button>}
        style={{ flex: '1 1 250px' }}>
        {reminderError && <p role="alert">{reminderError}</p>}
        {reminders.length === 0 && !reminderError && <Empty description="No pending reminders" />}
        <List dataSource={reminders} renderItem={(r: any) => (
          <List.Item actions={[
            <Button size="small" disabled={dismissingReminder !== null} onClick={() => handleMarkRead(r.id)}>Dismiss</Button>,
          ]}>
            <List.Item.Meta title={r.message} description={r.remind_at} />
          </List.Item>
        )} />
      </Card>

      {/* Create Plan Modal */}
      <Modal title="New Study Plan" open={createOpen} confirmLoading={creating} onOk={handleCreate} onCancel={() => setCreateOpen(false)}>
        <Input placeholder="Plan name" value={newName} onChange={e => setNewName(e.target.value)} style={{ marginBottom: 8 }} />
        <Input.TextArea placeholder="Description (optional)" value={newDesc} onChange={e => setNewDesc(e.target.value)} style={{ marginBottom: 8 }} />
        <Suspense fallback={<Input placeholder="Target end date" disabled style={{ width: '100%', marginBottom: 8 }} />}>
          <DatePicker placeholder="Target end date" value={newEndDate ? dayjs(newEndDate) : null}
            style={{ width: '100%', marginBottom: 8 }} onChange={d => setNewEndDate(d ? d.toISOString() : null)} />
        </Suspense>
        <Input.TextArea placeholder="Goals (one per line)" value={newGoals} onChange={e => setNewGoals(e.target.value)} rows={4} />
      </Modal>
    </div>
  )
}
