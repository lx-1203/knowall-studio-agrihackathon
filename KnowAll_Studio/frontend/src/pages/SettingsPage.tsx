import { useState, useEffect, useRef, useCallback } from 'react'
import { Card, Button, Input, Select, Space, App, Table, Tag, Statistic, Row, Col, Switch, List, Modal, Popconfirm, Typography, Skeleton } from 'antd'
import { DatabaseOutlined, BarChartOutlined, DeleteOutlined, ToolOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { listMCPServers, addMCPServer, removeMCPServer, listBuiltinTools, clearAdminCache } from '../api'
import { readAccountUsage, readProviderBalance, readServerCache, readAccountCalls } from '../services/accountMetrics'
import type { AccountUsage, ProviderBalance, ServerCache } from '../services/accountMetrics'
import './SettingsMetrics.css'

import { formatLocalDateTime } from '../services/flashcardTime'
import LoadFailure from '../components/LoadFailure'
import APIConfigurationPanel from '../components/APIConfigurationPanel'

const { Text } = Typography
type Resource = 'quota' | 'balance' | 'cache' | 'logs' | 'servers' | 'tools'
type ReadStatus = { pending: boolean; error: unknown }
const resources: Resource[] = ['quota', 'balance', 'cache', 'logs', 'servers', 'tools']

export default function SettingsPage() {
  const [quota, setQuota] = useState<AccountUsage | null>(null)
  const [balance, setBalance] = useState<ProviderBalance | null>(null)
  const [cacheStats, setCacheStats] = useState<ServerCache | null>(null)
  const { message } = App.useApp()
  const [apiLogs, setApiLogs] = useState<any[]>([])
  const [reads, setReads] = useState<Record<Resource, ReadStatus>>({
    quota: { pending: true, error: null }, balance: { pending: true, error: null }, cache: { pending: true, error: null },
    logs: { pending: true, error: null }, servers: { pending: true, error: null }, tools: { pending: true, error: null },
  })
  const controllers = useRef<Partial<Record<Resource, AbortController>>>({})
  const mounted = useRef(false)
  const [clearingCache, setClearingCache] = useState(false)
  const [mcpServers, setMcpServers] = useState<any[]>([])
  const [builtinTools, setBuiltinTools] = useState<any[]>([])
  const [mcpModalOpen, setMcpModalOpen] = useState(false)
  const [mcpName, setMcpName] = useState('')
  const [mcpCommand, setMcpCommand] = useState('')
  const [mcpUrl, setMcpUrl] = useState('')
  const [mcpTransport, setMcpTransport] = useState('stdio')

  // Page adapter only: shared Axios owns bearer, bounded safe-GET retries and cancellation.
  const loadResource = useCallback(async (key: Resource) => {
    if (!mounted.current) return
    controllers.current[key]?.abort()
    const next = new AbortController()
    controllers.current[key] = next
    const current = () => !next.signal.aborted && controllers.current[key] === next
    const config = { signal: next.signal }
    setReads(previous => ({ ...previous, [key]: { pending: true, error: null } }))
    try {
      const requests = {
        quota: () => readAccountUsage(config), balance: () => readProviderBalance(config), cache: () => readServerCache(config),
        logs: () => readAccountCalls(20, config), servers: () => listMCPServers(config), tools: () => listBuiltinTools(config),
      }
      const data = await requests[key]()
      if (!current()) return
      if (key === 'quota') setQuota(data as AccountUsage)
      else if (key === 'balance') setBalance(data as ProviderBalance)
      else if (key === 'cache') setCacheStats(data as ServerCache)
      else if (key === 'logs') setApiLogs(Array.isArray(data) ? data : [])
      else if (key === 'servers') setMcpServers((data as { servers?: any[] }).servers || [])
      else setBuiltinTools((data as { tools?: any[] }).tools || [])
    } catch (error) {
      if (current()) setReads(previous => ({ ...previous, [key]: { pending: false, error } }))
    } finally {
      if (current()) {
        setReads(previous => ({ ...previous, [key]: { ...previous[key], pending: false } }))
        delete controllers.current[key]
      }
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    resources.forEach(key => { void loadResource(key) })
    return () => {
      mounted.current = false
      Object.values(controllers.current).forEach(controller => controller?.abort())
    }
  }, [loadResource])
  const loadMCPServers = () => loadResource('servers')
  const loadBuiltinTools = () => loadResource('tools')
  const handleAddMCPServer = async () => {
    if (!mcpName.trim()) { message.warning('请输入服务器名称'); return }
    try {
      await addMCPServer({ name: mcpName, transport: mcpTransport, command: mcpCommand, url: mcpUrl })
      message.success('MCP 服务器已添加')
      setMcpModalOpen(false)
      setMcpName(''); setMcpCommand(''); setMcpUrl('')
      loadMCPServers()
    } catch (e: any) { message.error('添加失败') }
  }
  const handleRemoveMCPServer = async (name: string) => {
    try { await removeMCPServer(name); message.success('已删除'); loadMCPServers() } catch { message.error('删除失败') }
  }

  const loadLogs = () => loadResource('logs')

  const handleClearCache = async () => {
    setClearingCache(true)
    try {
      await clearAdminCache()
      message.success('缓存已清除')
      void loadResource('cache')
    } catch { message.error('清除失败') }
    finally { setClearingCache(false) }
  }

  return (
    <div>
      <Button size="small" aria-label="刷新统计与余额" icon={<ReloadOutlined />}
        style={{ marginBottom: 12 }} disabled={reads.quota.pending || reads.balance.pending || reads.cache.pending}
        onClick={() => { void loadResource('quota'); void loadResource('balance'); void loadResource('cache'); void loadResource('logs') }}>刷新统计与余额</Button>
      <div aria-label="真实来源统计" className="settings-metrics">
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24} md={8}><Card>{reads.quota.error ? <LoadFailure inline title="用量信息加载失败" error={reads.quota.error} onRetry={() => { void loadResource('quota') }} retryLabel="重新加载用量" /> : <>
            <Statistic title="今日系统记录" loading={reads.quota.pending} value={quota?.tokens ?? '—'} suffix={quota ? 'tokens' : undefined} />
            {quota && <p className="settings-metric-note">当前系统账号（所有模型连接） · {quota.day} · UTC{quota.utc_offset_minutes >= 0 ? '+' : ''}{quota.utc_offset_minutes / 60}<br />输入 {quota.input_tokens.toLocaleString()} + 输出 {quota.output_tokens.toLocaleString()}；{quota.calls} 次调用（含失败）。{quota.unmetered_calls > 0 && `其中 ${quota.unmetered_calls} 次缺少用量数据。`}<span className="settings-metric-time">统计于 {formatLocalDateTime(quota.checked_at)}</span></p>}
          </>}</Card></Col>
          <Col xs={24} md={8}><Card>{reads.balance.error ? <LoadFailure inline title="余额查询失败" error={reads.balance.error} onRetry={() => { void loadResource('balance') }} retryLabel="重查余额" /> : <>
            <Statistic title="服务商余额" loading={reads.balance.pending} value={balance?.status === 'available' ? balance.balances.map(row => row.total_balance + ' ' + row.currency).join(' / ') : balance?.status === 'unsupported' ? '暂不支持' : '未获取'} formatter={value => value} />
            {balance && <p className="settings-metric-note">{balance.status === 'available' ? <>{balance.connection_name} · 服务商接口实时返回。CNY 为人民币，USD 为美元。{balance.is_available === false && '当前余额不可用于调用。'}</> : balance.message}<span className="settings-metric-time">{balance.checked_at && '查询于 ' + formatLocalDateTime(balance.checked_at)}</span></p>}
          </>}</Card></Col>
          <Col xs={24} md={8}><Card>{reads.cache.error ? <LoadFailure inline title="缓存信息加载失败" error={reads.cache.error} onRetry={() => { void loadResource('cache') }} retryLabel="重新加载缓存信息" /> : <>
            <Statistic title="服务端有效缓存" loading={reads.cache.pending} value={cacheStats?.active_entries ?? '—'} suffix={cacheStats ? '条' : undefined} />
            {cacheStats && <p className="settings-metric-note">本服务全部账号的本地生成结果缓存；已保存 {cacheStats.stored_entries} 条，过期 {cacheStats.expired_entries} 条{cacheStats.invalid_entries ? `，无效 ${cacheStats.invalid_entries} 条` : ''}。<span className="settings-metric-time">统计于 {formatLocalDateTime(cacheStats.checked_at)}</span></p>}
          </>}</Card></Col>
        </Row>
        <p className="settings-metric-note">系统记录仅统计本服务已保存的生成调用，不含连接测试、其他应用调用及接口未上报的用量；token 是文字用量单位，不能按固定比例换算成余额。缓存条数也不是服务商的缓存命中次数。</p>
      </div>

      <APIConfigurationPanel onChanged={() => { void loadResource('balance') }} />

      <Card title={<Space><DatabaseOutlined /> 资料保存与连接说明</Space>} style={{ marginBottom: 16 }}>
        <ul style={{ lineHeight: 2 }}>
          <li>资料与学习记录保存在当前连接的服务中。</li>
          <li>使用云端服务时由云端保存；使用本机服务时由本机保存。</li>
          <li>云端资料和学习操作需要连接服务；断网后请恢复连接再继续。</li>
          <li>生成内容时，所选模型服务会处理提交的文本。</li>
        </ul>
      </Card>

      <Card title={<Space><BarChartOutlined /> API 调用日志</Space>}
        extra={<Space>
          <Button size="small" onClick={loadLogs} loading={reads.logs.pending} disabled={reads.logs.pending}>刷新</Button>
          <Button size="small" icon={<DeleteOutlined />} danger loading={clearingCache} onClick={handleClearCache}>清除缓存</Button>
        </Space>}
        style={{ marginBottom: 16 }}>
        {reads.logs.error ? <LoadFailure inline title="调用日志加载失败" error={reads.logs.error} onRetry={loadLogs} retryLabel="重新加载调用日志" /> : <Table
          dataSource={apiLogs}
          rowKey="id"
          loading={reads.logs.pending}
          size="small"
          pagination={{ pageSize: 5 }}
          scroll={{ x: 850 }}
          columns={[
            { title: '任务', dataIndex: 'task_type', width: 100, render: (v: string) => {
              const labels: Record<string, string> = { knowledge_tree: '知识树', quiz_gen: '出题', flashcard_gen: '闪卡', game_gen: '游戏', chat: '对话' }
              return <Tag>{labels[v] || v}</Tag>
            }},
            { title: '模型', dataIndex: 'model_name', width: 120 },
            { title: '输入', dataIndex: 'tokens_input', width: 70 },
            { title: '输出', dataIndex: 'tokens_output', width: 70 },
            { title: '耗时', dataIndex: 'duration_ms', width: 80, render: (v: number) => `${v}ms` },
            { title: '状态', dataIndex: 'success', width: 60, render: (v: boolean) => v ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag> },
            { title: '缓存', dataIndex: 'from_cache', width: 60, render: (v: boolean) => v ? <Tag color="blue">缓存</Tag> : <Tag>实时</Tag> },
            { title: '时间', dataIndex: 'created_at', width: 180, render: formatLocalDateTime },
          ]}
        />}
      </Card>

      {/* MCP Tools Section */}
      <Card
        title={<Space><ToolOutlined /> MCP 工具管理</Space>}
        extra={<Space>
          <Button size="small" icon={<ReloadOutlined />} disabled={reads.servers.pending || reads.tools.pending}
            loading={reads.servers.pending || reads.tools.pending} onClick={() => { loadMCPServers(); loadBuiltinTools() }}>刷新</Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setMcpModalOpen(true)}>添加 MCP 服务器</Button>
        </Space>}
        style={{ marginBottom: 16 }}
      >
        <Row gutter={16}>
          <Col span={12}>
            <Card size="small" title="内置工具" bodyStyle={{ padding: 8 }}>
              {reads.tools.error ? <LoadFailure inline title="内置工具加载失败" error={reads.tools.error} onRetry={loadBuiltinTools} retryLabel="重新加载内置工具" /> : <Skeleton active loading={reads.tools.pending}><List
                size="small"
                dataSource={builtinTools}
                renderItem={(t: any) => (
                  <List.Item style={{ padding: '4px 8px' }}>
                    <div>
                      <Text strong>{t.name}</Text>
                      <br />
                      <Text type="secondary" style={{ fontSize: 12 }}>{t.description}</Text>
                    </div>
                  </List.Item>
                )}
              /></Skeleton>}
            </Card>
          </Col>
          <Col span={12}>
            <Card size="small" title="MCP 服务器" bodyStyle={{ padding: 8 }}>
              {reads.servers.error ? <LoadFailure inline title="MCP 服务器加载失败" error={reads.servers.error} onRetry={loadMCPServers} retryLabel="重新加载 MCP 服务器" /> : <Skeleton active loading={reads.servers.pending}>{mcpServers.length === 0 ? (
                <Text type="secondary" style={{ padding: 8, display: 'block' }}>暂无外部 MCP 服务器</Text>
              ) : (
                <List
                  size="small"
                  dataSource={mcpServers}
                  renderItem={(s: any) => (
                    <List.Item
                      style={{ padding: '4px 8px' }}
                      actions={[
                        <Popconfirm title="确认删除？" onConfirm={() => handleRemoveMCPServer(s.name)}>
                          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                        </Popconfirm>,
                      ]}
                    >
                      <div>
                        <Text strong>{s.name}</Text>
                        <Tag style={{ marginLeft: 8 }}>{s.transport}</Tag>
                        {s.transport === 'stdio' && <Text type="secondary" style={{ fontSize: 12 }}> {s.command}</Text>}
                        {s.transport === 'sse' && <Text type="secondary" style={{ fontSize: 12 }}> {s.url}</Text>}
                      </div>
                    </List.Item>
                  )}
                />
              )}</Skeleton>}
            </Card>
          </Col>
        </Row>
      </Card>

      {/* Add MCP Server Modal */}
      <Modal
        title="添加 MCP 服务器"
        open={mcpModalOpen}
        onOk={handleAddMCPServer}
        onCancel={() => setMcpModalOpen(false)}
        okText="添加"
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text>服务器名称</Text>
            <Input placeholder="例如: my-tools" value={mcpName} onChange={e => setMcpName(e.target.value)} />
          </div>
          <div>
            <Text>传输方式</Text>
            <Select value={mcpTransport} onChange={setMcpTransport} style={{ width: '100%' }}
              options={[{ value: 'stdio', label: 'stdio (本地命令)' }, { value: 'sse', label: 'SSE (HTTP)' }]} />
          </div>
          {mcpTransport === 'stdio' && (
            <div>
              <Text>命令</Text>
              <Input placeholder="例如: npx @modelcontextprotocol/server-filesystem" value={mcpCommand} onChange={e => setMcpCommand(e.target.value)} />
            </div>
          )}
          {mcpTransport === 'sse' && (
            <div>
              <Text>URL</Text>
              <Input placeholder="例如: http://localhost:3001/sse" value={mcpUrl} onChange={e => setMcpUrl(e.target.value)} />
            </div>
          )}
        </Space>
      </Modal>
    </div>
  )
}
