import { authHeaderValue } from '../authToken'
import { consumeJSONEventStream } from './jsonEventStream'

export interface AgentSource { citation_id: string; document_id: string; chunk_id: string; filename: string; page_range: string; text: string; chunk_sha256: string; truncated: boolean }
export interface AgentStep { seq: number; type: string; tool?: string; code?: string; call_id?: string; at?: string }
export interface AgentModel { value: string; label: string; model_tool_support: 'unverified' }
export interface AgentReceipt { sources: AgentSource[]; steps: AgentStep[]; model: string; run_id?: string; live?: boolean; background?: boolean; cancelRequested?: boolean }
export interface AgentAnswer extends AgentReceipt { message: string; conversation_id: string; status: 'completed'; scope: 'readonly_documents'; assistant_message_id?: string }
export interface StoredAgentReceipt extends AgentReceipt { run_id: string; assistant_message_id: string; status: 'completed'; scope: 'readonly_documents' }
export interface AgentHistory { receipts: StoredAgentReceipt[]; has_more: boolean }
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string' && Boolean(v.trim())
const eventTypes = new Set(['run_started', 'step_started', 'tool_started', 'tool_completed', 'tool_failed', 'run_completed', 'run_failed', 'run_cancelled'])
const utcTime = (v: unknown): v is string => typeof v === 'string'
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(v)
  && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 19) === v.slice(0, 19)
export function validSteps(v: unknown): v is AgentStep[] {
  return Array.isArray(v) && v.length <= 30 && v.every((s, i) => object(s) && s.seq === i + 1 && typeof s.type === 'string' && eventTypes.has(s.type)
    && (s.tool === undefined || typeof s.tool === 'string') && (s.code === undefined || typeof s.code === 'string')
    && (s.call_id === undefined || typeof s.call_id === 'string') && (s.at === undefined || utcTime(s.at)))
}
function validSource(v: unknown): v is AgentSource {
  return object(v) && ['citation_id', 'document_id', 'chunk_id', 'filename'].every(k => text(v[k]))
    && typeof v.page_range === 'string' && typeof v.text === 'string' && typeof v.chunk_sha256 === 'string' && /^[a-f\d]{64}$/.test(v.chunk_sha256) && typeof v.truncated === 'boolean'
}
const failures: Record<string, string> = {
  UNVERIFIED_SOURCES: '没有获得可核验的资料引用，请调整问题后重试。',
  MODEL_REQUEST_FAILED: '模型请求失败，请检查配置后重试。',
  RUN_TIMEOUT: '资料任务超时，请缩小问题范围后重试。',
  MODEL_BUDGET_EXCEEDED: '本次资料任务达到执行上限，请缩小问题范围。',
  TOOL_BUDGET_EXCEEDED: '本次资料读取达到上限，请缩小问题范围。',
  CONTEXT_TOO_LARGE: '对话过长，请新建对话后继续。',
  EVENT_PERSISTENCE_FAILED: '执行进度暂时无法保存，任务已中断，请先核对运行状态。',
  PERSISTENCE_FAILED: '回答暂时无法保存，请先核对运行状态。',
  QUOTA_EXCEEDED: '模型用量不足，资料任务未完成。',
  REQUEST_CANCELLED: '资料任务已取消。',
}
export class AgentRequestError extends Error {
  constructor(reason: string, public steps: AgentStep[] = []) { super(reason) }
}
async function request(path: string, signal: AbortSignal, body?: unknown): Promise<unknown> {
  const response = await fetch(`/api/v1/agent/${path}`, { signal, method: body ? 'POST' : 'GET',
    headers: { Authorization: authHeaderValue(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}) })
  const data: unknown = await response.json().catch(() => null)
  signal.throwIfAborted()
  if (!response.ok) {
    const detail = object(data) ? data.detail : null
    const reason = typeof detail === 'string' ? detail : object(detail) && typeof detail.code === 'string' ? failures[detail.code] || '资料任务未完成，请调整问题后重试。'
      : response.status === 401 ? '请登录后使用资料 Agent。' : response.status === 404 ? '来源不存在或当前账号无权读取。' : '请求失败，请稍后重试。'
    throw new AgentRequestError(reason, object(detail) && validSteps(detail.steps) ? detail.steps : [])
  }
  return data
}
export async function listAgentModels(signal: AbortSignal): Promise<AgentModel[]> {
  return (await listAgentConfiguration(signal)).models
}
export async function listAgentConfiguration(signal: AbortSignal): Promise<{ models: AgentModel[]; defaultModel: string; streamingEvents: boolean; backgroundRuns: boolean; inputCheckpoints: boolean }> {
  const data = await request('models', signal)
  if (!object(data) || !Array.isArray(data.models) || !data.models.every(m => object(m) && text(m.value) && text(m.label) && m.model_tool_support === 'unverified')
    || (data.streaming_events !== undefined && typeof data.streaming_events !== 'boolean')
    || (data.background_runs !== undefined && typeof data.background_runs !== 'boolean')
    || (data.input_checkpoints !== undefined && typeof data.input_checkpoints !== 'boolean')) throw new AgentRequestError('模型清单格式异常，请重新加载。')
  return { models: data.models as AgentModel[], defaultModel: typeof data.default_model === 'string' ? data.default_model : '', streamingEvents: data.streaming_events === true, backgroundRuns: data.background_runs === true, inputCheckpoints: data.input_checkpoints === true }
}
function parseAnswer(data: unknown): AgentAnswer {
  if (!object(data) || data.status !== 'completed' || data.scope !== 'readonly_documents' || !text(data.message) || !text(data.conversation_id) || !text(data.model)
    || !Array.isArray(data.sources) || !data.sources.length || data.sources.length > 30 || !data.sources.every(validSource) || !validSteps(data.steps)
    || data.steps[data.steps.length - 1]?.type !== 'run_completed'
    || (data.run_id !== undefined && !text(data.run_id)) || (data.assistant_message_id !== undefined && !text(data.assistant_message_id))) throw new AgentRequestError('资料任务结果不完整，请重新提问。')
  return data as unknown as AgentAnswer
}
type AgentInput = { message: string; conversation_id: string | null; model: string }
export async function answerWithAgent(body: AgentInput, signal: AbortSignal): Promise<AgentAnswer> {
  return parseAnswer(await request('answer', signal, body))
}
/** A failed or partial stream is never retried as another model request. */
export async function streamAnswerWithAgent(body: AgentInput, signal: AbortSignal, onProgress: (runId: string, steps: AgentStep[]) => void): Promise<AgentAnswer> {
  const steps: AgentStep[] = []; let runId = ''; let answer: AgentAnswer | undefined
  try {
    const response = await fetch('/api/v1/agent/answer/stream', { method: 'POST', signal,
      headers: { Authorization: authHeaderValue(), 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const completed = await consumeJSONEventStream(response, data => {
      if (data.kind === 'step') {
        const event = data.event
        if (!text(data.run_id) || data.run_id.length > 36 || (runId && data.run_id !== runId)
          || !object(event) || !utcTime(event.at) || !validSteps([...steps, event])
          || (!steps.length && event.type !== 'run_started')
          || steps.some(s => ['run_completed', 'run_failed', 'run_cancelled'].includes(s.type))) throw new AgentRequestError('执行进度格式异常，任务完成状态需要核对。', [...steps])
        runId = data.run_id; steps.push(event as unknown as AgentStep); onProgress(runId, [...steps])
      } else if (data.kind === 'result') {
        const result = parseAnswer(data.result)
        if (!runId || data.run_id !== runId || result.run_id !== runId || !text(result.assistant_message_id)
          || result.steps.length !== steps.length || !result.steps.every((s, i) => ['seq', 'type', 'at', 'tool', 'code', 'call_id'].every(k => s[k as keyof AgentStep] === steps[i][k as keyof AgentStep]))) throw new AgentRequestError('回答与已保存的执行记录不一致，请核对运行状态。', [...steps])
        answer = result; return true
      } else if (data.kind === 'error') {
        if ((data.run_id !== undefined && (!text(data.run_id) || (runId && data.run_id !== runId))) || !text(data.code)
          || !/^[A-Z0-9_]{1,64}$/.test(data.code) || !validSteps(data.steps)
          || data.steps.length < steps.length || data.steps.some(s => s.type === 'run_completed')
          || !steps.every((s, i) => ['seq', 'type', 'at', 'tool', 'code', 'call_id'].every(k => s[k as keyof AgentStep] === (data.steps as AgentStep[])[i][k as keyof AgentStep]))) throw new AgentRequestError('任务错误记录格式异常，请核对运行状态。', [...steps])
        throw new AgentRequestError(failures[data.code] || '资料任务未完成，请核对运行状态。', data.steps)
      } else throw new AgentRequestError('执行进度格式异常，任务完成状态需要核对。', [...steps])
      return false
    }, { signal })
    if (!completed || !answer) throw new AgentRequestError('连接提前结束，任务完成状态未确认。', [...steps])
    return answer
  } catch (error) {
    if (signal.aborted) throw error
    if (error instanceof AgentRequestError) throw error
    throw new AgentRequestError(error instanceof Error ? error.message : '执行连接中断，请核对运行状态。', [...steps])
  }
}

export interface ObservedAgentRun extends AgentReceipt {
  run_id: string; conversation_id: string | null; assistant_message_id: string | null
  status: 'queued' | 'running' | 'waiting_user' | 'completed' | 'partial' | 'failed' | 'cancelled'
  recovery_state?: 'active' | 'terminal' | 'unconfirmed'; error_code: string | null
  execution_mode?: 'background' | 'request'; cancel_requested?: boolean; created_at?: string; input_saved?: boolean
}
function validRun(data: unknown): data is ObservedAgentRun {
  return object(data) && text(data.run_id) && data.run_id.length <= 36 && data.scope === 'readonly_documents' && text(data.model)
    && typeof data.status === 'string' && ['queued', 'running', 'waiting_user', 'completed', 'partial', 'failed', 'cancelled'].includes(data.status)
    && (data.recovery_state === undefined || typeof data.recovery_state === 'string' && ['active', 'terminal', 'unconfirmed'].includes(data.recovery_state))
    && (data.execution_mode === undefined || ['background', 'request'].includes(String(data.execution_mode)) && typeof data.execution_mode === 'string')
    && (data.cancel_requested === undefined || typeof data.cancel_requested === 'boolean') && (data.created_at === undefined || utcTime(data.created_at))
    && (data.input_saved === undefined || typeof data.input_saved === 'boolean')
    && (data.conversation_id === null || text(data.conversation_id)) && (data.assistant_message_id === null || text(data.assistant_message_id))
    && (data.error_code === null || text(data.error_code)) && validSteps(data.steps)
    && Array.isArray(data.sources) && data.sources.length <= 30 && data.sources.every(validSource)
    && (data.status !== 'completed' || text(data.conversation_id) && text(data.assistant_message_id) && data.sources.length > 0 && data.steps[data.steps.length - 1]?.type === 'run_completed')
}
export async function readAgentRun(runId: string, signal: AbortSignal): Promise<ObservedAgentRun> {
  const data = await request(`runs/${encodeURIComponent(runId)}`, signal)
  if (!validRun(data) || data.run_id !== runId) throw new AgentRequestError('运行记录格式异常，请重新读取。')
  return data
}
export interface SavedAgentInput { run_id: string; message: string; requested_model: string; created_at: string; version: 1 }
export async function readSavedAgentInput(runId: string, signal: AbortSignal): Promise<SavedAgentInput> {
  const data = await request(`runs/${encodeURIComponent(runId)}/input`, signal)
  if (!object(data) || data.run_id !== runId || data.version !== 1 || !text(data.message) || data.message.length > 8000
    || !text(data.requested_model) || data.requested_model.length > 256 || !utcTime(data.created_at)) throw new AgentRequestError('保存输入格式异常，请重新读取。')
  return data as unknown as SavedAgentInput
}
export async function deleteSavedAgentInput(runId: string, signal: AbortSignal): Promise<void> {
  const response = await fetch(`/api/v1/agent/runs/${encodeURIComponent(runId)}/input`, { method: 'DELETE', signal, headers: { Authorization: authHeaderValue() } })
  signal.throwIfAborted()
  if (response.status === 204) return
  const data: unknown = await response.json().catch(() => null)
  signal.throwIfAborted()
  throw new AgentRequestError(object(data) && typeof data.detail === 'string' ? data.detail : '删除输入尚未确认，请重试。')
}
export async function listAgentRuns(offset: number, signal: AbortSignal): Promise<{ runs: ObservedAgentRun[]; hasMore: boolean }> {
  const data = await request(`runs?limit=20&offset=${offset}`, signal)
  if (!object(data) || !Array.isArray(data.runs) || data.runs.length > 20 || !data.runs.every(validRun)
    || new Set(data.runs.map(r => r.run_id)).size !== data.runs.length || typeof data.has_more !== 'boolean') throw new AgentRequestError('任务清单格式异常，请重新读取。')
  return { runs: data.runs, hasMore: data.has_more }
}
export async function cancelBackgroundRun(runId: string, signal: AbortSignal): Promise<void> {
  const data = await request(`runs/${encodeURIComponent(runId)}/cancel`, signal, {})
  if (!object(data) || data.run_id !== runId || typeof data.status !== 'string' || !['running', 'queued', 'completed', 'failed', 'cancelled', 'partial', 'waiting_user'].includes(data.status)
    || typeof data.cancel_requested !== 'boolean' || (!data.cancel_requested && ['queued', 'running', 'waiting_user'].includes(data.status))) throw new AgentRequestError('停止请求尚未确认，请重新读取运行状态。')
}
function observationDelay(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted()
    const abort = () => { window.clearTimeout(timer); reject(signal.reason) }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 500)
    signal.addEventListener('abort', abort, { once: true })
  })
}
type BackgroundProgress = (runId: string, steps: AgentStep[], cancelRequested: boolean, model: string) => void
export async function observeBackgroundAnswer(runId: string, signal: AbortSignal, onProgress: BackgroundProgress): Promise<AgentAnswer> {
  let known: AgentStep[] = []
  try {
    while (true) {
      const record = await readAgentRun(runId, signal)
      if (record.execution_mode !== 'background' || record.steps.length < known.length
        || !known.every((s, i) => ['seq', 'type', 'at', 'tool', 'code', 'call_id'].every(k => s[k as keyof AgentStep] === record.steps[i][k as keyof AgentStep]))) throw new AgentRequestError('任务进度与已保存记录不一致，请核对状态。', known)
      known = record.steps; onProgress(runId, known, record.cancel_requested === true, record.model)
      if (record.status === 'completed') {
        const result = parseAnswer(await request(`runs/${encodeURIComponent(runId)}/result`, signal))
        if (result.run_id !== runId || result.assistant_message_id !== record.assistant_message_id || result.conversation_id !== record.conversation_id
          || result.steps.length !== known.length || !result.steps.every((s, i) => ['seq', 'type', 'at', 'tool', 'code', 'call_id'].every(k => s[k as keyof AgentStep] === known[i][k as keyof AgentStep]))) throw new AgentRequestError('回答与已保存的执行记录不一致，请核对运行状态。', known)
        return result
      }
      if (['failed', 'cancelled', 'partial', 'waiting_user'].includes(record.status)) throw new AgentRequestError(record.status === 'cancelled' ? '资料任务已取消。' : failures[record.error_code || ''] || '资料任务未完成，请核对运行状态。', known)
      if (record.recovery_state === 'unconfirmed') throw new AgentRequestError('长时间没有记录新进度，任务状态需要核对。', known)
      await observationDelay(signal)
    }
  } catch (error) {
    if (signal.aborted || error instanceof AgentRequestError) throw error
    throw new AgentRequestError(error instanceof Error ? error.message : '任务观察中断，请重新读取运行状态。', known)
  }
}
export async function startBackgroundAnswer(body: AgentInput & { persist_input?: boolean }, signal: AbortSignal, onProgress: BackgroundProgress): Promise<AgentAnswer> {
  let data: unknown
  try { data = await request('runs', signal, body) }
  catch (error) {
    if (signal.aborted) throw error
    throw new AgentRequestError('任务提交未确认，请先查看任务记录，避免重复发送。')
  }
  if (!object(data) || !text(data.run_id) || data.run_id.length > 36 || data.execution_mode !== 'background') throw new AgentRequestError('任务提交未确认，请先查看任务记录，避免重复发送。')
  onProgress(data.run_id, [], false, body.model)
  return observeBackgroundAnswer(data.run_id, signal, onProgress)
}
const observationKey = (userId: string) => `knowall_agent_observed_run:${userId}`
export function rememberObservedRun(userId: string, runId: string) {
  try { localStorage.setItem(observationKey(userId), JSON.stringify({ run_id: runId })) } catch { /* Server records remain readable from history. */ }
}
export function observedRunId(userId: string): string | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(observationKey(userId)) || 'null')
    return object(value) && text(value.run_id) && value.run_id.length <= 36 ? value.run_id : null
  } catch { return null }
}
export function forgetObservedRun(userId: string, runId: string) {
  try { if (observedRunId(userId) === runId) localStorage.removeItem(observationKey(userId)) } catch { /* Local storage may be disabled. */ }
}
export async function readAgentHistory(conversationId: string, signal: AbortSignal): Promise<AgentHistory> {
  const data = await request(`conversations/${encodeURIComponent(conversationId)}/receipts`, signal)
  if (!object(data) || !Array.isArray(data.receipts) || data.receipts.length > 100 || typeof data.has_more !== 'boolean'
    || !data.receipts.every(r => object(r) && text(r.run_id) && text(r.assistant_message_id) && r.status === 'completed' && r.scope === 'readonly_documents' && text(r.model)
      && Array.isArray(r.sources) && r.sources.length > 0 && r.sources.length <= 30 && r.sources.every(validSource) && validSteps(r.steps) && r.steps[r.steps.length - 1]?.type === 'run_completed')
    || new Set(data.receipts.map(r => r.assistant_message_id)).size !== data.receipts.length
    || new Set(data.receipts.map(r => r.run_id)).size !== data.receipts.length) throw new AgentRequestError('历史执行记录格式异常，请重新加载。')
  return data as unknown as AgentHistory
}
export async function readAgentSource(source: AgentSource, signal: AbortSignal): Promise<AgentSource> {
  const data = await request(`sources/${encodeURIComponent(source.chunk_id)}?sha256=${encodeURIComponent(source.chunk_sha256)}`, signal)
  if (!validSource(data) || data.chunk_id !== source.chunk_id || data.document_id !== source.document_id || data.chunk_sha256 !== source.chunk_sha256
    || data.filename !== source.filename || data.page_range !== source.page_range) throw new AgentRequestError('来源版本无法核对，请重新提问。')
  return data
}
