import { useEffect, useState } from 'react'
import { Alert, App, Button, Card, Empty, Form, Input, InputNumber, Modal, Popconfirm, Select, Skeleton, Space, Switch, Table, Tag } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import api from '../../api'
import { consolePath, count, errorText, localTime, useAdminRead, type AdminKey, type CallLog, type ModelConfig } from './adminApi'

function Failure({ error, retry }: { error: unknown; retry: () => void }) {
  return <Alert type="error" showIcon message="数据加载失败" description={errorText(error)} action={<Button onClick={retry}>重试</Button>} />
}

export function AdminSystem() {
  const health = useAdminRead<Record<string, unknown>>('/health')
  const cache = useAdminRead<{ total_entries: number; total_tokens_saved: number }>('/cache/stats')
  const quota = useAdminRead<{ daily_limit: number; used_today: number; remaining: number }>('/quota/status')
  const [clearing, setClearing] = useState(false)
  const { message } = App.useApp()
  const clear = async () => {
    setClearing(true)
    try { await api.delete(`${consolePath}/cache/clear`); message.success('缓存已清除'); cache.reload() }
    catch (error) { message.error(errorText(error)) }
    finally { setClearing(false) }
  }
  const serviceStatus = (value: unknown) => {
    if (value === 'ok' || value && typeof value === 'object' && 'status' in value && value.status === 'ok') return <Tag color="success">正常</Tag>
    if (value && typeof value === 'object' && !('status' in value)) return <Tag color="blue">已连接</Tag>
    if (value === undefined || value === null) return <Tag>未报告</Tag>
    return <Tag color="warning">需检查</Tag>
  }
  return <div className="admin-system-grid">
    <Card title="服务健康" extra={<Button aria-label="刷新服务健康" type="text" icon={<ReloadOutlined />} loading={health.pending} onClick={health.reload} />}>
      {health.error ? <Failure error={health.error} retry={health.reload} /> : health.pending ? <Skeleton active /> : <><div className="admin-service-row"><strong>总体状态</strong>{serviceStatus(health.data?.status)}</div>{[['database', '数据库'], ['vector_store', '向量检索'], ['graphrag', '知识图谱'], ['api_scheduler', '模型调度']].map(([key, label]) => <div className="admin-service-row" key={key}><span>{label}</span>{serviceStatus(health.data?.[key])}</div>)}<p className="admin-hint">版本 {String(health.data?.version || '未报告')}{health.data?.timestamp ? ` · ${localTime(String(health.data.timestamp))}` : ''}</p></>}
    </Card>
    <Card title="缓存管理">{cache.error ? <Failure error={cache.error} retry={cache.reload} /> : cache.pending ? <Skeleton active /> : <><div className="admin-inline-stats"><div><span>缓存条目</span><strong>{count(cache.data?.total_entries)}</strong></div><div><span>已缓存输出（tokens）</span><strong>{count(cache.data?.total_tokens_saved)}</strong></div></div><p className="admin-hint">清除后，相同请求需要重新计算；已保存的资料和题目不受影响。</p><Popconfirm title="确认清除所有模型响应缓存？" description="之后的请求可能需要重新调用模型。" onConfirm={clear} okText="确认清除" cancelText="取消" disabled={clearing}><Button danger loading={clearing}>清除缓存</Button></Popconfirm></>}</Card>
    <Card title="当前管理员账户额度">{quota.error ? <Failure error={quota.error} retry={quota.reload} /> : quota.pending ? <Skeleton active /> : <><div className="admin-inline-stats"><div><span>今日已使用</span><strong>{count(quota.data?.used_today)}</strong></div><div><span>今日剩余</span><strong>{count(quota.data?.remaining)}</strong></div></div><p className="admin-hint">每日上限 {count(quota.data?.daily_limit)} tokens（模型处理的文本单位）。此处只统计当前登录账户。</p></>}</Card>
  </div>
}

const providers = ['deepseek', 'openai', 'anthropic', 'qwen', 'zhipu', 'ollama']

export function AdminModels() {
  const keys = useAdminRead<AdminKey[]>('/keys')
  const config = useAdminRead<ModelConfig>('/models')
  const [keyForm] = Form.useForm()
  const [modelForm] = Form.useForm()
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [keyPending, setKeyPending] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const { message } = App.useApp()
  useEffect(() => {
    if (config.data) modelForm.setFieldsValue({ ...config.data, fallback_models: config.data.fallback_models.join(', ') })
  }, [config.data, modelForm])
  const add = async () => {
    const values = await keyForm.validateFields().catch(() => null)
    if (!values) return
    setAdding(true)
    try {
      await api.post(`${consolePath}/keys`, { ...values, api_key: values.api_key.trim(), key_alias: values.key_alias?.trim(), permission_level: 'personal' })
      message.success('密钥已添加'); keyForm.resetFields(); setOpen(false); keys.reload()
    } catch (error) { message.error(errorText(error)) }
    finally { setAdding(false) }
  }
  const mutateKey = async (id: string, action: 'toggle' | 'delete') => {
    setKeyPending(id)
    try {
      if (action === 'toggle') await api.put(`${consolePath}/keys/${encodeURIComponent(id)}/toggle`)
      else await api.delete(`${consolePath}/keys/${encodeURIComponent(id)}`)
      message.success(action === 'toggle' ? '密钥状态已更新' : '密钥已删除'); keys.reload()
    } catch (error) { message.error(errorText(error)) }
    finally { setKeyPending(null) }
  }
  const save = async (values: { model: string; fallback_models: string; daily_token_limit: number }) => {
    setSaving(true)
    try {
      await api.post(`${consolePath}/models`, { ...values, model: values.model.trim(), fallback_models: values.fallback_models.split(',').map(value => value.trim()).filter(Boolean) })
      message.success('模型配置已保存'); config.reload()
    } catch (error) { message.error(errorText(error)) }
    finally { setSaving(false) }
  }
  return <div className="admin-models">
    <Card title="模型服务密钥" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>添加密钥</Button>}>
      <p className="admin-hint">管理平台保存的模型连接凭据。列表仅显示掩码，完整密钥不会回显。</p>
      {keys.error ? <Failure error={keys.error} retry={keys.reload} /> : <Table<AdminKey> rowKey="id" loading={keys.pending} dataSource={keys.data || []} scroll={{ x: 650 }} pagination={{ pageSize: 8, showSizeChanger: false }} locale={{ emptyText: <Empty description="尚未配置模型密钥" /> }} columns={[
        { title: '服务商', dataIndex: 'provider', render: value => <strong>{value}</strong> },
        { title: '密钥名称', dataIndex: 'key_alias', render: value => value || '未命名' },
        { title: '密钥掩码', dataIndex: 'key_masked', render: value => <code>{value}</code> },
        { title: '状态', render: (_, row) => <Switch aria-label={`${row.key_alias || row.provider}密钥状态`} checkedChildren="启用" unCheckedChildren="停用" checked={row.is_active} loading={keyPending === row.id} disabled={keyPending !== null || keys.pending} onChange={() => void mutateKey(row.id, 'toggle')} /> },
        { title: '操作', render: (_, row) => <Popconfirm title={`确认删除密钥「${row.key_alias || row.provider}」？`} description="删除后需要重新添加密钥才能恢复此连接。" okText="确认删除" cancelText="取消" onConfirm={() => mutateKey(row.id, 'delete')}><Button danger type="text" icon={<DeleteOutlined />} disabled={keyPending !== null || keys.pending}>删除</Button></Popconfirm> },
      ]} />}
    </Card>
    <Card title="默认模型与额度"><p className="admin-hint">默认模型用于没有传入独立生成配置的通用请求；各工具的既有配置优先。额度上限用于没有独立额度记录的账户，已有账户额度不会被覆盖。</p>{!!config.error && <><Failure error={config.error} retry={config.reload} /><p className="admin-hint">读取失败，仍可填写并保存新配置进行修复。保存会替换已有配置记录。</p></>}{config.pending ? <Skeleton active /> : <Form form={modelForm} layout="vertical" onFinish={save} className="admin-config-form" initialValues={{ fallback_models: '', daily_token_limit: 1000000 }}>
      <Form.Item name="model" label="默认模型" rules={[{ required: true, whitespace: true, message: '请输入默认模型名称' }, { max: 100, message: '模型名称不能超过100个字符' }]}><Input placeholder="例如 deepseek-chat" /></Form.Item>
      <Form.Item name="fallback_models" label="备用模型" extra="多个模型用英文逗号分隔；留空表示不配置备用模型。"><Input placeholder="例如 gpt-4o-mini, qwen-plus" /></Form.Item>
      <Form.Item name="daily_token_limit" label="每日处理上限（tokens）" rules={[{ required: true, message: '请输入每日上限' }]}><InputNumber min={1} max={1000000000} precision={0} style={{ width: '100%' }} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={saving}>保存模型配置</Button>
    </Form>}</Card>
    <Modal title="添加模型密钥" open={open} onCancel={() => { if (!adding) { setOpen(false); keyForm.resetFields() } }} onOk={() => void add()} okText="保存密钥" cancelText="取消" confirmLoading={adding} cancelButtonProps={{ disabled: adding }} closable={!adding} maskClosable={!adding}>
      <Form form={keyForm} layout="vertical" initialValues={{ provider: 'deepseek' }}>
        <Form.Item name="provider" label="模型服务商" rules={[{ required: true }]}><Select options={providers.map(value => ({ value, label: value }))} /></Form.Item>
        <Form.Item name="key_alias" label="密钥名称" rules={[{ max: 100, message: '名称最多100个字符' }]}><Input placeholder="例如 主服务密钥" autoComplete="off" /></Form.Item>
        <Form.Item name="api_key" label="API 密钥" rules={[{ required: true, whitespace: true, message: '请输入 API 密钥' }]}><Input.Password autoComplete="new-password" placeholder="粘贴模型服务商提供的密钥" /></Form.Item>
      </Form>
    </Modal>
  </div>
}

export function AdminLogs() {
  const logs = useAdminRead<CallLog[]>('/logs?limit=500')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const rows = (logs.data || []).filter(row => `${row.model_name} ${row.task_type}`.toLowerCase().includes(search.toLowerCase()) && (status === 'all' || status === 'success' && row.success || status === 'failed' && !row.success || status === 'cache' && row.from_cache))
  const exportCSV = () => {
    // Quoting alone does not prevent spreadsheet formula execution.
    const cell = (value: unknown) => `"${String(value ?? '').replace(/^(?:\s*[=+@-]|[\t\r\n])/, "'$&").replace(/"/g, '""')}"`
    const lines = [['时间', '任务', '模型', '输入tokens', '输出tokens', '耗时ms', '成功', '缓存'], ...rows.map(row => [localTime(row.created_at), row.task_type, row.model_name, row.tokens_input, row.tokens_output, row.duration_ms, row.success ? '是' : '否', row.from_cache ? '是' : '否'])]
    const url = URL.createObjectURL(new Blob(['\uFEFF' + lines.map(line => line.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = 'knowall-call-logs.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <Card title="模型调用日志" extra={<Button icon={<ReloadOutlined />} loading={logs.pending} onClick={logs.reload}>刷新日志</Button>}>
    <p className="admin-hint">展示全平台最新 500 条调用摘要。筛选与导出仅作用于这些记录；不含提问正文或密钥。</p>
    <div className="admin-log-toolbar"><Space wrap><Input.Search type="search" aria-label="搜索调用日志" placeholder="搜索模型或任务类型" allowClear value={search} onChange={event => setSearch(event.target.value)} style={{ width: 240, maxWidth: '100%' }} /><Select aria-label="日志状态" value={status} onChange={setStatus} style={{ width: 140 }} options={[{ value: 'all', label: '全部状态' }, { value: 'success', label: '调用成功' }, { value: 'failed', label: '调用失败' }, { value: 'cache', label: '命中缓存' }]} /></Space><Button disabled={!rows.length || logs.pending} onClick={exportCSV}>导出筛选结果</Button></div>
    {logs.error ? <Failure error={logs.error} retry={logs.reload} /> : <Table<CallLog> rowKey="id" loading={logs.pending} dataSource={rows} scroll={{ x: 850 }} pagination={{ defaultPageSize: 10, showSizeChanger: true, showTotal: total => `共 ${total} 条` }} locale={{ emptyText: <Empty description="暂无符合条件的调用记录" /> }} columns={[
      { title: '调用时间', dataIndex: 'created_at', render: localTime, width: 175 },
      { title: '模型 / 任务', render: (_, row) => <><strong>{row.model_name}</strong><div className="admin-hint">{row.task_type}</div></> },
      { title: '处理量', render: (_, row) => <>{count((row.tokens_input || 0) + (row.tokens_output || 0))}<div className="admin-hint">输入 {count(row.tokens_input)} / 输出 {count(row.tokens_output)}</div></> },
      { title: '耗时', dataIndex: 'duration_ms', render: value => `${count(value)} ms`, sorter: (a, b) => a.duration_ms - b.duration_ms },
      { title: '状态', render: (_, row) => <Tag color={row.success ? 'success' : 'error'}>{row.success ? '成功' : '失败'}</Tag> },
      { title: '缓存', dataIndex: 'from_cache', render: value => value ? <Tag color="cyan">命中</Tag> : '未命中' },
    ]} />}
  </Card>
}
