import { useEffect, useRef, useState } from 'react'
import { Alert, AutoComplete, Button, Card, Collapse, Empty, Form, Input, List, Modal, Popconfirm, Select, Space, Switch, Tag, Typography } from 'antd'
import { ApiOutlined, PlusOutlined } from '@ant-design/icons'
import { readModelConnections, saveModelConnection, copyLegacyConnection, removeModelConnection, chooseDefaultConnection, fetchConnectionModels, probeModelConnection, configurationError } from '../modelConnectionsApi'
import type { ConnectionDraft, ModelConfiguration, ModelConnection } from '../modelConnectionsApi'
import { formatLocalDateTime } from '../services/flashcardTime'
import './APIConfigurationPanel.css'

const blank: ConnectionDraft = { name: '', provider: 'custom', protocol: 'openai', base_url: '', api_key: '', model: '', is_active: true }

export default function APIConfigurationPanel({ onChanged }: { onChanged?: () => void } = {}) {
  const [data, setData] = useState<ModelConfiguration | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [version, setVersion] = useState(0)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<ModelConnection | null>(null)
  const [formError, setFormError] = useState('')
  const [form] = Form.useForm<ConnectionDraft>()
  const mounted = useRef(true)
  const mutation = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    readModelConnections({ signal: controller.signal }).then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(configurationError(reason)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [version])
  const reload = () => setVersion(v => v + 1)
  const edit = (row: ModelConnection | null) => {
    setEditing(row); setFormError(''); setNotice('')
    form.setFieldsValue(row ? { name: row.name, provider: row.provider, protocol: row.protocol, base_url: row.base_url, model: row.model, is_active: row.is_active, api_key: '' } : { ...blank })
    setOpen(true)
  }
  const run = async (id: string, action: () => Promise<string | void>) => {
    if (mutation.current) return
    mutation.current = true; setBusy(id); setError(''); setNotice('')
    try {
      const result = await action()
      if (mounted.current) { setNotice(result || '配置已更新'); reload(); if (!id.endsWith(':models')) onChanged?.() }
    } catch (reason) { if (mounted.current) setError(configurationError(reason)) }
    finally { mutation.current = false; if (mounted.current) setBusy('') }
  }
  const save = async (draft: ConnectionDraft) => {
    if (mutation.current) return
    mutation.current = true; setBusy('save'); setFormError('')
    try {
      await saveModelConnection({ ...draft, api_key: draft.api_key || '', model: draft.model || '' }, editing?.id)
      if (mounted.current) { setOpen(false); form.resetFields(); setNotice('连接已保存。可获取模型列表，再测试生成或设为默认。'); reload(); onChanged?.() }
    } catch (reason) { if (mounted.current) setFormError(configurationError(reason)) }
    finally { mutation.current = false; if (mounted.current) setBusy('') }
  }
  const preset = (provider: string) => {
    const next = data?.presets.find(p => p.provider === provider)
    if (next) {
      const previousName = form.getFieldValue('name')
      form.setFieldsValue({ provider, base_url: next.base_url, protocol: next.protocol, ...(!previousName ? { name: next.name } : {}) })
    }
  }
  const selected = data?.connections.find(row => row.model_ref && row.model_ref === data.default_model)
  const toggle = (row: ModelConnection, value: boolean) => run(row.id + ':toggle', async () => {
    await saveModelConnection({ name: row.name, provider: row.provider, protocol: row.protocol, base_url: row.base_url, model: row.model, api_key: '', is_active: value }, row.id)
    return value ? '连接已启用' : '连接已停用；不会静默换用其他连接。'
  })
  return <Card className="api-configuration" title={<Space><ApiOutlined />AI 模型连接</Space>} extra={<Button aria-label="添加连接" icon={<PlusOutlined />} type="primary" disabled={!!busy || loading || !data} onClick={() => edit(null)}>添加连接</Button>}>
    <p>填写服务地址、密钥和接口协议，再选择模型。自定义网关可使用自己的名称；密钥归属不能通过字符串自动判定。</p>
    <div className="api-config-default" aria-label="当前默认模型">
      <strong>默认生成模型</strong>
      <span>{selected ? selected.name + ' / ' + selected.model : data?.default_source === 'legacy' ? '沿用原有配置 / ' + data.default_model : data?.default_model ? '原默认连接已不可用，请重新选择' : '尚未设置个人默认模型'}</span>
      {selected && <Tag color={selected.is_active ? 'green' : 'orange'}>{selected.is_active ? '已应用' : '已停用，生成会被阻止'}</Tag>}
    </div>
    <p className="material-muted">默认模型用于资料学习包、导图、卡片、练习和问答。单次任务明确选择模型时，以单次选择为准。</p>
    {error && <Alert type="error" message={error} showIcon action={<Button onClick={reload} disabled={!!busy}>重新加载</Button>} />}
    {notice && <Alert type="info" message={notice} showIcon closable onClose={() => setNotice('')} />}
    <List loading={loading} dataSource={data?.connections || []} locale={{ emptyText: error ? '配置尚未加载' : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="添加一个模型连接，或从下方历史配置创建个人副本" /> }} renderItem={row => <List.Item className="api-config-row" key={row.id}>
      <article aria-label={'模型连接 ' + row.name}>
        <div className="api-config-row-title"><Space wrap><strong>{row.name}</strong><Tag>{row.provider}</Tag><Tag>{row.protocol === 'anthropic' ? 'Anthropic Messages 协议' : 'OpenAI 兼容协议'}</Tag>{selected?.id === row.id && <Tag color="blue">默认</Tag>}</Space><Switch aria-label={row.name + '连接启用'} checked={row.is_active} disabled={!!busy} onChange={value => void toggle(row, value)} /></div>
        <dl><div><dt>接口地址</dt><dd>{row.base_url}</dd></div><div><dt>API Key</dt><dd>{row.key_masked}</dd></div><div><dt>模型 ID</dt><dd>{row.model || '尚未选择，可获取模型或手动填写'}</dd></div></dl>
        {row.fetched_at && <p className="material-muted">服务商可用模型：{row.models.length} 个 · 获取于 {formatLocalDateTime(row.fetched_at)}</p>}
        {row.last_test && <Alert type={row.last_test.status === 'success' ? 'success' : 'warning'} message={row.last_test.message} description={'测试模型 ' + row.last_test.model + ' · ' + formatLocalDateTime(row.last_test.checked_at) + ' · ' + row.last_test.latency_ms + ' ms'} />}
        <Space wrap>
          <Button disabled={!!busy} onClick={() => edit(row)}>编辑</Button>
          <Button loading={busy === row.id + ':models'} disabled={!!busy || !row.is_active} onClick={() => void run(row.id + ':models', async () => {
            const result = await fetchConnectionModels(row.id)
            if (result.errors?.length) throw new Error(result.errors.map(e => e.message).join('；'))
            return '已获取 ' + result.models.length + ' 个可用模型。点击“编辑”选择模型并保存；这不代表已验证生成能力。'
          })}>获取模型</Button>
          <Button loading={busy === row.id + ':test'} disabled={!!busy || !row.is_active || !row.model} onClick={() => void run(row.id + ':test', async () => { const result = await probeModelConnection(row.id); return result.message })}>测试生成</Button>
          <Button type={selected?.id === row.id ? 'default' : 'primary'} disabled={!!busy || !row.is_active || !row.model || selected?.id === row.id} onClick={() => void run(row.id + ':default', async () => { await chooseDefaultConnection(row); return '默认模型已保存，后续生成将使用此连接。' })}>设为默认</Button>
          <Popconfirm title="移除此连接？" description="不会删除原有资料。移除默认连接后，需要重新选择默认模型。" onConfirm={() => run(row.id + ':remove', async () => { await removeModelConnection(row.id); return '连接已移除' })} okText="移除" cancelText="取消"><Button danger disabled={!!busy}>移除</Button></Popconfirm>
        </Space>
      </article>
    </List.Item>} />
    <p className="material-muted">“获取模型”只读取该接口提供的清单，不表示全网最新发布。“测试生成”会发送一条很短的验证请求，可能产生少量费用。</p>
    <Collapse defaultActiveKey={['legacy']} items={[{ key: 'legacy', label: '历史密钥与服务器配置（只读，原配置保留）', children: <>
      <p>历史记录只有服务商标签，接口地址按旧预设展示，不能据此鉴别密钥归属。创建个人副本后，请核对地址和协议，再获取模型。</p>
      {data?.legacy_keys.map(row => <div key={row.id} className="api-config-legacy"><div><strong>{row.name}</strong><Tag>{row.scope === 'server' ? '服务器历史密钥' : '个人历史密钥'}</Tag><p>{row.provider} · {row.base_url || '地址未知'}</p></div><Button disabled={!!busy || !row.is_active} onClick={() => void run(row.id + ':copy', async () => { await copyLegacyConnection(row.id); return '已创建个人副本，原记录保留。请编辑核对地址与协议。' })}>创建个人副本</Button></div>)}
      {data?.server_connections.map((row, i) => <p key={i}>服务器地址：<Typography.Text code>{row.base_url}</Typography.Text> · 协议 {row.protocol} · 当前配置模型 {row.model}</p>)}
      {!data?.legacy_keys.length && !data?.server_connections.length && <Empty description="没有历史配置" />}
    </> }]} />
    <Modal title={editing ? '编辑模型连接' : '添加模型连接'} open={open} onCancel={() => { if (!busy) { setOpen(false); form.resetFields() } }} maskClosable={!busy} closable={!busy} destroyOnClose footer={<Button type="primary" loading={busy === 'save'} disabled={!!busy && busy !== 'save'} onClick={() => form.submit()}>保存连接</Button>}>
      <Form form={form} layout="vertical" onFinish={save} initialValues={blank} disabled={!!busy}>
        <Form.Item name="name" label="连接名称" rules={[{ required: true, whitespace: true, message: '请给这个连接起个名字' }]}><Input aria-label="连接名称" maxLength={100} placeholder="例如：我的模型网关" /></Form.Item>
        <Form.Item name="provider" label="服务商标记" extra="仅用于预填与辨认，不根据 Key 猜测服务商。"><Select aria-label="服务商标记" options={(data?.presets || []).map(p => ({ value: p.provider, label: p.name }))} onChange={preset} /></Form.Item>
        <Form.Item name="protocol" label="接口协议" extra="Claude 模型也可能通过 OpenAI 兼容网关提供，请按你的服务文档选择。"><Select aria-label="接口协议" options={[{ value: 'openai', label: 'OpenAI 兼容（Chat Completions）' }, { value: 'anthropic', label: 'Anthropic Messages' }]} /></Form.Item>
        <Form.Item name="base_url" label="API 基础地址" rules={[{ required: true, whitespace: true, message: '请填写接口基础地址' }]} extra="例如 https://服务地址/v1；不要包含 /chat/completions、/messages 或 /models。"><Input aria-label="API 基础地址" placeholder="https://your-provider.example/v1" maxLength={500} /></Form.Item>
        <Form.Item name="api_key" label="API Key" extra={editing?.has_key ? '密钥已加密保存，留空表示保留。修改地址时必须重新输入。' : 'Ollama 本机服务可留空。'}><Input.Password aria-label="API Key" autoComplete="new-password" maxLength={256} placeholder={editing?.has_key ? '已保存；留空保留原密钥' : '粘贴此服务的 API Key'} /></Form.Item>
        <Form.Item name="model" label="模型 ID" extra="可先留空保存，再获取模型列表。不支持列表接口时，可按服务商文档手动填写。"><AutoComplete aria-label="模型 ID" options={(editing?.models || []).map(m => ({ value: m.model_id, label: m.display_name + ' · ' + m.model_id }))} filterOption={(input, option) => String(option?.label).toLowerCase().includes(input.toLowerCase())} maxLength={100} placeholder="模型调用 ID" /></Form.Item>
        <Form.Item name="is_active" label="启用连接" valuePropName="checked"><Switch aria-label="启用此连接" /></Form.Item>
      </Form>
      {formError && <Alert type="error" message={formError} />}
    </Modal>
  </Card>
}
