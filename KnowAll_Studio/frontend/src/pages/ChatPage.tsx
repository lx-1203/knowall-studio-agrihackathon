import { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react'
import { Button, Input, Select, App, Switch, Modal, Alert, Skeleton, Drawer, Grid } from 'antd'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import { SendOutlined, RobotOutlined, UserOutlined, PlusOutlined, SearchOutlined, FileTextOutlined, HistoryOutlined, CopyOutlined, StopOutlined, ArrowRightOutlined, BookOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { listConversations, getConversation, listRoles } from '../api'
import { useAppStore, useAuthStore } from '../stores'
import LoadFailure from '../components/LoadFailure'
import { authHeaderValue } from '../authToken'
import { consumeJSONEventStream, StreamHTTPError } from '../services/jsonEventStream'
import { formatFlashcardDate } from '../services/flashcardTime'
import { answerWithAgent, streamAnswerWithAgent, startBackgroundAnswer, observeBackgroundAnswer, cancelBackgroundRun, listAgentConfiguration, readAgentHistory, rememberObservedRun, forgetObservedRun, AgentRequestError, type AgentModel, type AgentReceipt } from '../services/nativeAgent'
import './ChatPage.css'

interface Message { id: string; role: string; content: string; created_at: string; failure?: string; stopped?: boolean; readonlyAgent?: boolean; agent?: AgentReceipt; serverMessageId?: string }
interface Conv { id: string; title: string; role_preset: string; created_at: string; }
type ReadKey = 'conversations' | 'roles' | 'detail'
type ReadStatus = { pending: boolean; error: unknown }

const apiBase = '/api/v1'
const AgentAnswerReceipt = lazy(() => import('../components/AgentAnswerReceipt'))
const AgentRunRecovery = lazy(() => import('../components/AgentRunRecovery'))
const AgentRunList = lazy(() => import('../components/AgentRunList'))
// The full document renderer is needed only when the reader is opened.
const DocumentViewer = lazy(() => import('../components/DocumentViewer'))

let _msgIdCounter = 0
function nextMsgId() { return `${Date.now()}_${++_msgIdCounter}_${Math.random().toString(36).slice(2, 8)}` }

export default function ChatPage() {
  const { selectedDoc } = useAppStore()
  const userId = useAuthStore(state => state.user?.id || '')
  const { message } = App.useApp()
  const [convs, setConvs] = useState<Conv[]>([])
  const [currentConv, setCurrentConv] = useState<Conv | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [role, setRole] = useState('tutor')
  const [roles, setRoles] = useState<Record<string, { name: string }>>({})
  const [loading, setLoading] = useState(false)
  const [streamMode, setStreamMode] = useState(true)
  const [ragMode, setRagMode] = useState(false)
  const [agentMode, setAgentMode] = useState(false)
  const [agentModels, setAgentModels] = useState<AgentModel[]>([])
  const [agentModel, setAgentModel] = useState('')
  const [agentStreaming, setAgentStreaming] = useState(false)
  const [agentCanBackground, setAgentCanBackground] = useState(false)
  const [agentBackground, setAgentBackground] = useState(false)
  const [agentCanSaveInput, setAgentCanSaveInput] = useState(false)
  const [agentSaveInput, setAgentSaveInput] = useState(false)
  const [runListOpen, setRunListOpen] = useState(false)
  const [inspectedRun, setInspectedRun] = useState('')
  const [stopPending, setStopPending] = useState(false); const [stopError, setStopError] = useState('')
  const activeBackground = useRef<{ id: string; controller: AbortController } | null>(null)
  const stopController = useRef<AbortController | null>(null)
  const [agentRead, setAgentRead] = useState({ pending: false, error: '' })
  const modelController = useRef<AbortController | null>(null)
  const [docViewerOpen, setDocViewerOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyQuery, setHistoryQuery] = useState('')
  const [showLatest, setShowLatest] = useState(false)
  const screens = Grid.useBreakpoint()
  const compact = !screens.md
  const inputRef = useRef<TextAreaRef>(null)
  const runListButton = useRef<HTMLButtonElement | HTMLAnchorElement | null>(null)
  const composing = useRef(false)
  const focusAfterStop = useRef(false)
  const activeMessage = useRef<string | null>(null)
  const [reads, setReads] = useState<Record<ReadKey, ReadStatus>>({
    conversations: { pending: true, error: null }, roles: { pending: true, error: null }, detail: { pending: false, error: null },
  })
  const readControllers = useRef<Partial<Record<ReadKey, AbortController>>>({})
  const messagePanel = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const activeRequest = useRef<AbortController | null>(null)
  useEffect(() => () => { activeRequest.current?.abort(); stopController.current?.abort() }, [userId])
  const receiptController = useRef<AbortController | null>(null)
  const selectedConversation = useRef<string | null>(null)
  const [receiptRead, setReceiptRead] = useState({ pending: false, error: null as unknown, hasMore: false })
  useEffect(() => () => receiptController.current?.abort(), [])
  const loadReceipts = useCallback(async (conversationId: string) => {
    receiptController.current?.abort()
    const next = new AbortController(); receiptController.current = next
    const owns = () => !next.signal.aborted && receiptController.current === next && selectedConversation.current === conversationId
    setReceiptRead({ pending: true, error: null, hasMore: false })
    try {
      const data = await readAgentHistory(conversationId, next.signal)
      if (!owns()) return
      const byMessage = new Map(data.receipts.map(r => [r.assistant_message_id, r]))
      setMessages(previous => previous.map(m => {
        const receipt = m.role === 'assistant' ? byMessage.get(m.serverMessageId || m.id) : undefined
        return receipt ? { ...m, readonlyAgent: true, agent: receipt } : m
      }))
      setReceiptRead({ pending: false, error: null, hasMore: data.has_more })
    } catch (error) {
      if (owns()) setReceiptRead({ pending: false, error, hasMore: false })
    } finally {
      if (receiptController.current === next) receiptController.current = null
    }
  }, [])

  const loadModels = useCallback(async () => {
    modelController.current?.abort()
    const next = new AbortController(); modelController.current = next
    setAgentRead({ pending: true, error: '' })
    try {
      const configuration = await listAgentConfiguration(next.signal)
      const models = configuration.models
      if (modelController.current !== next || next.signal.aborted) return
      setAgentModels(models)
      setAgentStreaming(configuration.streamingEvents)
      setAgentCanBackground(configuration.backgroundRuns)
      setAgentCanSaveInput(configuration.inputCheckpoints)
      if (!configuration.inputCheckpoints) setAgentSaveInput(false)
      if (!configuration.backgroundRuns) setAgentBackground(false)
      setAgentModel(previous => models.some(m => m.value === previous) ? previous : configuration.defaultModel || models[0]?.value || '')
    } catch (cause) {
      if (modelController.current === next && !next.signal.aborted) {
        setAgentModels([]); setAgentModel(''); setAgentStreaming(false); setAgentCanBackground(false); setAgentBackground(false); setAgentCanSaveInput(false); setAgentSaveInput(false)
        setAgentRead({ pending: true, error: cause instanceof Error ? cause.message : '模型加载失败' })
      }
    } finally {
      if (modelController.current === next && !next.signal.aborted) {
        modelController.current = null
        setAgentRead(previous => ({ ...previous, pending: false }))
      }
    }
  }, [])
  useEffect(() => {
    if (agentMode) void loadModels()
    return () => modelController.current?.abort()
  }, [agentMode, loadModels])

  // Native Axios signal/retry remains shared; this adapter owns only page read state.
  const loadRead = useCallback(async (key: ReadKey, target?: Conv) => {
    readControllers.current[key]?.abort()
    const next = new AbortController()
    readControllers.current[key] = next
    const current = () => !next.signal.aborted && readControllers.current[key] === next
    setReads(previous => ({ ...previous, [key]: { pending: true, error: null } }))
    if (target) {
      receiptController.current?.abort(); receiptController.current = null
      selectedConversation.current = target.id
      setReceiptRead({ pending: false, error: null, hasMore: false })
      setCurrentConv(target); setMessages([])
    }
    try {
      const config = { signal: next.signal }
      const data = key === 'conversations' ? await listConversations(config)
        : key === 'roles' ? await listRoles(config) : await getConversation(target!.id, config)
      if (!current()) return
      if (key === 'conversations') setConvs(data)
      else if (key === 'roles') setRoles(data)
      else {
        setCurrentConv({ id: data.conversation_id, title: data.title, role_preset: data.role_preset, created_at: target!.created_at })
        setMessages(data.messages || [])
        setRole(data.role_preset || 'tutor')
        void loadReceipts(target!.id)
      }
    } catch (error) {
      if (current()) setReads(previous => ({ ...previous, [key]: { pending: true, error } }))
    } finally {
      if (current()) {
        setReads(previous => ({ ...previous, [key]: { ...previous[key], pending: false } }))
        delete readControllers.current[key]
      }
    }
  }, [loadReceipts])
  useEffect(() => {
    void loadRead('conversations'); void loadRead('roles')
    return () => Object.values(readControllers.current).forEach(controller => controller?.abort())
  }, [loadRead])

  useEffect(() => {
    if (messagePanel.current && followLatest.current) messagePanel.current.scrollTop = messages.length ? messagePanel.current.scrollHeight : 0
  }, [messages])
  useEffect(() => {
    if (!loading && focusAfterStop.current) {
      focusAfterStop.current = false
      inputRef.current?.focus()
    }
  }, [loading])

  const handleSend = async () => {
    if (!input.trim() || loading || activeRequest.current || readBlocked) return
    followLatest.current = true
    setShowLatest(false)

    const userMsg: Message = { id: nextMsgId(), role: 'user', content: input, created_at: new Date().toISOString() }
    setMessages(prev => [...prev, userMsg])
    const msgText = input
    setInput('')
    setLoading(true)

    const controller = new AbortController()
    activeRequest.current = controller
    activeBackground.current = agentMode && agentBackground && agentCanBackground ? { id: '', controller } : null
    setStopError(''); setStopPending(false)
    const aiMsgId = nextMsgId()
    activeMessage.current = aiMsgId
    setMessages(prev => [...prev, { id: aiMsgId, role: 'assistant', content: '', created_at: new Date().toISOString(), readonlyAgent: agentMode }])
    const endpoint = `${apiBase}/chat/assistant${ragMode ? '/rag' : ''}${streamMode ? '/stream' : ''}`
    try {
      let convId = currentConv?.id
      if (agentMode) {
        const body = { message: msgText, conversation_id: currentConv?.id || null, model: agentModel }
        const progress = (runId: string, steps: AgentReceipt['steps'], cancelRequested = false) => {
          if (activeRequest.current !== controller || controller.signal.aborted) return
          if (userId) rememberObservedRun(userId, runId)
          if (activeBackground.current?.controller === controller) activeBackground.current.id = runId
          setMessages(previous => previous.map(m => m.id === aiMsgId ? { ...m, agent: { run_id: runId, sources: [], steps, model: agentModel, live: true,
            background: activeBackground.current?.controller === controller, cancelRequested: cancelRequested || m.agent?.cancelRequested } } : m))
        }
        const result = activeBackground.current ? await startBackgroundAnswer({ ...body, ...(agentCanSaveInput && agentSaveInput ? { persist_input: true } : {}) }, controller.signal, progress)
          : agentStreaming ? await streamAnswerWithAgent(body, controller.signal, progress) : await answerWithAgent(body, controller.signal)
        if (activeRequest.current !== controller || controller.signal.aborted) return
        convId = result.conversation_id
        if (userId && result.run_id) forgetObservedRun(userId, result.run_id)
        setMessages(prev => prev.map(m => m.id === aiMsgId ? { ...m, content: result.message, agent: result, serverMessageId: result.assistant_message_id } : m))
      } else {
      const response = await fetch(endpoint, {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', Authorization: authHeaderValue() },
        body: JSON.stringify({ message: msgText, conversation_id: currentConv?.id || null, role_preset: role }),
      })
      if (streamMode) {
        const completed = await consumeJSONEventStream(response, data => {
          if (activeRequest.current !== controller || controller.signal.aborted) return
          if (data.error) throw new Error(String(data.error))
          if (typeof data.token === 'string') {
            const token = data.token
            setMessages(prev => prev.map(m => m.id === aiMsgId ? { ...m, content: m.content + token } : m))
          }
          if (typeof data.conversation_id === 'string') convId = data.conversation_id
          return data.done === true
        }, { signal: controller.signal })
        if (!completed) throw new Error('连接提前结束')
      } else {
        if (!response.ok) throw new StreamHTTPError(response.status)
        const result = await response.json()
        if (result.error || typeof result.message !== 'string') throw new Error(result.error || '回复格式错误')
        controller.signal.throwIfAborted()
        convId = result.conversation_id
        setMessages(prev => prev.map(m => m.id === aiMsgId ? { ...m, content: result.message } : m))
      }
      }
      // Reader cleanup is asynchronous; a stopped request may finish after a
      // new conversation has started. Only the still-owned request may select it.
      if (controller.signal.aborted || activeRequest.current !== controller) return
      if (convId && !currentConv) {
        const conv = { id: convId, title: msgText.slice(0, 30), role_preset: agentMode ? 'tutor' : role, created_at: new Date().toISOString() }
        setCurrentConv(conv)
        selectedConversation.current = conv.id
        setConvs(prev => [conv, ...prev])
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        const reason = error instanceof Error ? error.message : '连接中断'
        const failure = error instanceof AgentRequestError || error instanceof StreamHTTPError && [401, 403].includes(error.status) ? reason : '回复未完成'
        setMessages(prev => prev.map(m => m.id === aiMsgId ? { ...m, failure,
          ...(error instanceof AgentRequestError && error.steps.length ? { agent: { sources: [], steps: error.steps, model: agentModel, run_id: m.agent?.run_id, live: false } }
            : m.agent ? { agent: { ...m.agent, live: false } } : {}) } : m))
        message.error(reason)
      }
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null
        activeMessage.current = null
        activeBackground.current = null; setStopPending(false)
        if (!controller.signal.aborted) setLoading(false)
      }
    }
  }

  const openRunConversation = (id: string) => {
    setInspectedRun('')
    const conv = convs.find(c => c.id === id) || { id, title: '已保存资料任务', role_preset: 'tutor', created_at: new Date().toISOString() }
    handleSelectConv(conv)
  }
  const resumeObservation = async (runId: string) => {
    if (loading || activeRequest.current) return
    const controller = new AbortController(); activeRequest.current = controller
    activeBackground.current = { id: runId, controller }; setStopError(''); setStopPending(false)
    const id = nextMsgId(); activeMessage.current = id; setLoading(true); setInspectedRun('')
    setMessages(previous => [...previous, { id, role: 'assistant', content: '', created_at: new Date().toISOString(), readonlyAgent: true }])
    try {
      const result = await observeBackgroundAnswer(runId, controller.signal, (observed, steps, cancelRequested, actualModel) => {
        if (activeRequest.current !== controller || controller.signal.aborted) return
        rememberObservedRun(userId, observed)
        setMessages(previous => previous.map(m => m.id === id ? { ...m, agent: { run_id: observed, sources: [], steps, model: actualModel, live: true, background: true, cancelRequested: cancelRequested || m.agent?.cancelRequested } } : m))
      })
      if (activeRequest.current !== controller || controller.signal.aborted) return
      forgetObservedRun(userId, runId)
      setMessages(previous => previous.map(m => m.id === id ? { ...m, content: result.message, agent: result, serverMessageId: result.assistant_message_id } : m))
      const conv = convs.find(c => c.id === result.conversation_id) || { id: result.conversation_id, title: '已保存资料任务', role_preset: 'tutor', created_at: new Date().toISOString() }
      await loadRead('detail', conv)
      void loadRead('conversations')
    } catch (cause) {
      if (activeRequest.current === controller && !controller.signal.aborted) setMessages(previous => previous.map(m => m.id === id ? { ...m, failure: cause instanceof Error ? cause.message : '任务观察中断', ...(m.agent ? { agent: { ...m.agent, live: false } } : {}) } : m))
    } finally {
      if (activeRequest.current === controller) { activeRequest.current = null; activeMessage.current = null; activeBackground.current = null; setLoading(false); setStopPending(false); focusAfterStop.current = true }
    }
  }

  const handleSelectConv = (conv: Conv) => {
    if (loading) return
    setHistoryOpen(false)
    followLatest.current = true
    setShowLatest(false)
    void loadRead('detail', conv)
  }
  const startNew = () => {
    if (loading) return
    setHistoryOpen(false)
    followLatest.current = true
    setShowLatest(false)
    readControllers.current.detail?.abort()
    delete readControllers.current.detail
    setReads(previous => ({ ...previous, detail: { pending: false, error: null } }))
    receiptController.current?.abort(); receiptController.current = null; selectedConversation.current = null
    setReceiptRead({ pending: false, error: null, hasMore: false })
    setCurrentConv(null); setMessages([])
  }
  const readBlocked = reads.conversations.pending || (agentMode ? agentRead.pending || Boolean(agentRead.error) || !agentModel : reads.roles.pending || Boolean(reads.roles.error) || !roles[role])
    || reads.detail.pending || Boolean(reads.detail.error)

  const stopReply = () => {
    if (activeBackground.current) { void stopBackground(); return }
    const id = activeMessage.current
    activeRequest.current?.abort()
    activeRequest.current = null
    activeMessage.current = null
    focusAfterStop.current = true
    setLoading(false)
    setMessages(previous => previous.map(m => m.id === id ? { ...m, stopped: true, ...(m.agent ? { agent: { ...m.agent, live: false } } : {}) } : m))
  }
  const stopBackground = async () => {
    const active = activeBackground.current
    if (!active?.id || stopPending) return
    const messageId = activeMessage.current
    const controller = new AbortController(); stopController.current = controller; setStopPending(true); setStopError('')
    try {
      await cancelBackgroundRun(active.id, controller.signal)
      if (activeRequest.current === active.controller && !controller.signal.aborted) {
        focusAfterStop.current = true
        setMessages(previous => previous.map(m => m.id === messageId && m.agent ? { ...m, agent: { ...m.agent, cancelRequested: true } } : m))
      }
    } catch (cause) {
      if (activeRequest.current === active.controller && !controller.signal.aborted) setStopError(cause instanceof Error ? cause.message : '停止请求尚未确认')
    } finally { if (!controller.signal.aborted && stopController.current === controller) { stopController.current = null; setStopPending(false) } }
  }
  const copyReply = async (content: string) => {
    try { await navigator.clipboard.writeText(content); message.success('回复已复制') }
    catch { message.error('复制失败，请手动选择文字复制') }
  }
  const suggestions = [
    { title: '梳理一个知识点', detail: '用解释和例子建立理解', prompt: '请用通俗的语言解释这个知识点，并给出一个具体例子：', icon: <BookOutlined /> },
    { title: '拆解一道难题', detail: '分步骤分析解题思路', prompt: '请引导我一步一步分析这道题，先不要直接告诉我答案：', icon: <SearchOutlined /> },
    { title: '检验我的理解', detail: '通过追问发现理解盲点', prompt: '请根据下面的内容向我提问，每次一道题，等我回答后再给反馈：', icon: <RobotOutlined /> },
  ]
  const filteredConvs = convs.filter(c => c.title.toLocaleLowerCase().includes(historyQuery.trim().toLocaleLowerCase()))
  const history = (
    <div className="chat-history-content">
      <div className="chat-history-heading"><h2>对话列表</h2><span>{convs.length} 个对话</span></div>
      <Button block icon={<PlusOutlined />} disabled={loading} onClick={startNew} className="chat-new">新对话</Button>
      <Input aria-label="搜索历史对话" placeholder="搜索历史对话" prefix={<SearchOutlined />} allowClear
        value={historyQuery} onChange={e => setHistoryQuery(e.target.value)} />
      <div className="chat-history-list">
        {reads.conversations.error ? <LoadFailure inline stacked title="对话列表加载失败" error={reads.conversations.error}
          onRetry={() => void loadRead('conversations')} retryLabel="重新加载对话列表" pending={reads.conversations.pending} />
          : reads.conversations.pending ? <Skeleton active /> : !convs.length ? <p className="chat-history-empty">暂无历史对话</p>
          : !filteredConvs.length ? <p className="chat-history-empty">没有匹配的对话</p> : filteredConvs.map(c => (
            <button type="button" key={c.id} className={`chat-history-item${currentConv?.id === c.id ? ' is-current' : ''}`}
              disabled={loading} aria-current={currentConv?.id === c.id ? 'true' : undefined} onClick={() => handleSelectConv(c)}>
              <span className="chat-history-title">{c.title}</span>
              <span className="chat-history-meta"><span>{roles[c.role_preset]?.name || c.role_preset}</span><time>{formatFlashcardDate(c.created_at)}</time></span>
            </button>
          ))}
      </div>
      <p className="chat-history-footer">每一次追问，都让理解更进一步。</p>
    </div>
  )

  return (
    <div className="chat-workspace">
      {!compact && <aside className="chat-history" aria-label="对话历史">{history}</aside>}
      {compact && <Drawer title="对话历史" placement="left" width="min(320px, 100vw)" open={historyOpen}
        onClose={() => setHistoryOpen(false)} styles={{ body: { padding: 0 } }} className="chat-history-drawer">{history}</Drawer>}
      <section className="chat-main" aria-label="AI 学习助手">
        <header className="chat-header">
          <div className="chat-header-title">
            {compact && <Button type="text" icon={<HistoryOutlined />} aria-label="打开对话历史" onClick={() => setHistoryOpen(true)} />}
            <span className="chat-assistant-mark"><RobotOutlined /></span>
            <div><h1>{currentConv?.title || 'AI 学习助手'}</h1><p>{currentConv ? '继续提问，深入理解' : '陪你阅读、理解与巩固'}</p></div>
          </div>
          <Select value={roles[role] ? role : undefined} placeholder="请选择角色" onChange={setRole}
            aria-label="助教角色" loading={reads.roles.pending} disabled={agentMode || loading || reads.roles.pending || Boolean(reads.roles.error)}
            options={Object.entries(roles).map(([k, v]) => ({ value: k, label: v.name }))} />
        </header>
        <div className="chat-mode-bar">
          <label><Switch aria-label="只读资料 Agent" checked={agentMode} onChange={setAgentMode} disabled={loading} size="small" />只读资料 Agent</label>
          <Button ref={runListButton} size="small" onClick={() => setRunListOpen(true)}>查看任务记录</Button>
          {!agentMode && <><label><Switch aria-label="检索学习资料" checked={ragMode} onChange={setRagMode} disabled={loading} size="small" />检索学习资料</label>
          <label><Switch aria-label="逐字显示回复" checked={streamMode} onChange={setStreamMode} disabled={loading} size="small" />逐字显示</label></>}
          {selectedDoc && <Button icon={<FileTextOutlined />} size="small" onClick={() => setDocViewerOpen(true)}>文档</Button>}
        </div>
        {agentMode && <div className="chat-agent-controls">
          <div><Select aria-label="Agent 模型" placeholder="选择已配置模型" value={agentModel || undefined} onChange={setAgentModel}
            loading={agentRead.pending} disabled={loading || agentRead.pending || !agentModels.length} options={agentModels.map(m => ({ value: m.value, label: m.label }))} />
            <span>自动检索、读取资料 · 模型需支持工具调用</span></div>
          {agentCanBackground && <div className="chat-agent-background-option"><label><Switch aria-label="离开页面后继续" checked={agentBackground} onChange={setAgentBackground} disabled={loading} size="small" />离开页面后继续</label><span>{agentBackground ? '任务会在后台执行；需要明确停止才会取消。' : '关闭时，离开页面会中断本次问答。'}</span></div>}
          {agentCanSaveInput && agentBackground && <div className="chat-agent-background-option"><label><Switch aria-label="加密保存任务输入" checked={agentSaveInput} onChange={setAgentSaveInput} disabled={loading} size="small" />加密保存任务输入</label><span>保存原问题和对话上下文，可手动取回；不会自动重新执行。</span></div>}
          {agentRead.error && <div role="alert"><span>{agentRead.error}</span><Button size="small" onClick={() => void loadModels()}>重新加载模型</Button></div>}
          {!agentRead.pending && !agentRead.error && !agentModels.length && <p>当前账号暂无可用模型。<a href="/settings">配置模型</a></p>}
        </div>}
        {stopError && <Alert className="chat-agent-stop-error" type="warning" showIcon message={stopError} />}
        <div className="chat-messages" ref={messagePanel} role="log" aria-label="对话消息" aria-live="polite"
          onScroll={e => {
            const panel = e.currentTarget
            const nearBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 64
            followLatest.current = nearBottom
            setShowLatest(!nearBottom)
          }}>
          {!agentMode && Boolean(reads.roles.error) && <LoadFailure inline title="角色加载失败" error={reads.roles.error}
            onRetry={() => void loadRead('roles')} retryLabel="重新加载角色" pending={reads.roles.pending} />}
          {!agentMode && !reads.roles.pending && !reads.roles.error && !Object.keys(roles).length && <p>暂无可用角色</p>}
          {Boolean(reads.detail.error) && <LoadFailure inline title="历史消息加载失败" error={reads.detail.error}
            onRetry={() => currentConv && void loadRead('detail', currentConv)} retryLabel="重新加载历史消息" pending={reads.detail.pending} />}
          {reads.detail.pending && <Skeleton active />}
          {userId && <Suspense fallback={null}><AgentRunRecovery key={`${userId}:${inspectedRun}`} userId={userId} initialRunId={inspectedRun || undefined} disabled={loading || reads.detail.pending}
            hasDraft={Boolean(input.trim())} onRestoreInput={question => { if (!input.trim() && !activeRequest.current) { setInput(question); requestAnimationFrame(() => inputRef.current?.focus()) } }}
            onOpen={openRunConversation} onResume={id => void resumeObservation(id)} onDismiss={() => requestAnimationFrame(() => runListButton.current?.focus({ preventScroll: true }))} /></Suspense>}
          {receiptRead.pending && <p role="status">正在加载已保存的执行记录…</p>}
          {Boolean(receiptRead.error) && <LoadFailure inline title="执行记录加载失败" error={receiptRead.error}
            onRetry={() => currentConv && void loadReceipts(currentConv.id)} retryLabel="重新加载执行记录" pending={receiptRead.pending} />}
          {receiptRead.hasMore && <p>已加载最近100条执行记录，较早记录暂未显示。</p>}
          {messages.length === 0 && !reads.detail.pending && !reads.detail.error && (
            <div className="chat-welcome">
              <span className="chat-welcome-kicker">把不懂的地方，变成新的收获</span>
              <h2>今天想弄懂什么？</h2>
              <p>从一个概念、一段原文或一道题开始。<br />你可以继续追问，按自己的节奏学习。</p>
              <div className="chat-suggestions">
                {suggestions.map(s => <Button key={s.title} disabled={loading || readBlocked} onClick={() => { setInput(s.prompt); inputRef.current?.focus() }}>
                  <span className="chat-suggestion-icon">{s.icon}</span><span><strong>{s.title}</strong><small>{s.detail}</small></span><ArrowRightOutlined />
                </Button>)}
              </div>
            </div>
          )}
          {messages.map(m => (
            <article key={m.id} className={`chat-message ${m.role === 'user' ? 'is-user' : 'is-assistant'}`}>
              <span className="chat-avatar" aria-hidden="true">{m.role === 'user' ? <UserOutlined /> : <RobotOutlined />}</span>
              <div className="chat-message-body">
                <div className="chat-message-byline"><span>{m.role === 'user' ? '你' : m.readonlyAgent ? '资料 Agent · 只读' : 'AI 助教'}</span></div>
                <div className="chat-message-content">
                {m.content ? (
                  m.role === 'assistant' ? (
                    <div className="chat-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {m.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <span className="chat-user-text">{m.content}</span>
                  )
                ) : (
                  loading && activeMessage.current === m.id && !m.failure ? <span className="chat-thinking" role="status">{m.readonlyAgent ? '正在检索与核对资料…' : '思考中...'}</span> : ''
                )}
                {m.failure && <Alert type="error" message={m.failure} description={m.readonlyAgent ? '本次资料任务未完成，可调整问题后再次发送。' : '已保留收到的内容，请检查连接后再发送。'} showIcon />}
                {m.agent && <Suspense fallback={<span role="status">加载来源与执行记录…</span>}><AgentAnswerReceipt receipt={m.agent} /></Suspense>}
                </div>
                {m.role === 'assistant' && <div className="chat-message-actions">
                  {m.stopped && <span role="status">{m.readonlyAgent ? '已停止资料任务' : '已停止回复'}</span>}
                  {m.content && <Button type="text" size="small" icon={<CopyOutlined />} aria-label="复制回复" onClick={() => void copyReply(m.content)}>复制</Button>}
                </div>}
              </div>
            </article>
          ))}
        </div>
        {showLatest && messages.length > 0 && <Button className="chat-latest" size="small" aria-label="回到最新"
          onClick={() => {
            followLatest.current = true
            setShowLatest(false)
            if (messagePanel.current) messagePanel.current.scrollTop = messagePanel.current.scrollHeight
          }}>回到最新</Button>}
        <div className="chat-composer">
          <Input.TextArea ref={inputRef} aria-label="输入学习问题" value={input} onChange={e => setInput(e.target.value)}
            onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !composing.current && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void handleSend() } }}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行..."
            autoSize={{ minRows: 2, maxRows: 5 }} disabled={loading || readBlocked} />
          <div className="chat-composer-footer"><span>{agentMode ? '仅检索与读取有权限的资料' : ragMode ? '会检索已导入的学习资料' : 'Enter 发送 · Shift + Enter 换行'}</span>
            {loading ? <Button aria-label={activeBackground.current ? '停止后台任务' : '停止回复'} loading={stopPending} disabled={Boolean(activeBackground.current && !activeBackground.current.id)} icon={<StopOutlined aria-hidden="true" />} onClick={stopReply}>{activeBackground.current ? '停止后台任务' : '停止回复'}</Button>
              : <Button icon={<SendOutlined />} type="primary" onClick={() => void handleSend()} disabled={readBlocked}>发送</Button>}
          </div>
          <p className="chat-composer-note">AI 回复可能有误，请结合教材核对。</p>
        </div>
      </section>
      {userId && <Suspense fallback={null}><AgentRunList key={userId} userId={userId} open={runListOpen} disabled={loading || reads.detail.pending} onClose={() => setRunListOpen(false)} onOpen={openRunConversation} onInspect={setInspectedRun} /></Suspense>}

      <Modal title="文档浏览" open={docViewerOpen} onCancel={() => setDocViewerOpen(false)}
        footer={null} width={800} destroyOnHidden>
        {selectedDoc && <Suspense fallback={<span role="status">正在打开文档…</span>}><DocumentViewer documentId={selectedDoc}
          onSearch={(text) => { setInput(text); setDocViewerOpen(false); setRagMode(true); }} /></Suspense>}
      </Modal>
    </div>
  )
}
