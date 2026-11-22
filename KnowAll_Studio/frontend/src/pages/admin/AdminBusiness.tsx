import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Card, Descriptions, Empty, Form, Input, Modal, Popconfirm, Select, Skeleton, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import api from '../../api'
import { useAuthStore } from '../../stores'
import { consolePath, errorText, localTime } from './adminApi'

type Kind = 'users' | 'documents' | 'questions'
type User = { id: string; username: string; email?: string; is_active: boolean; is_admin: boolean; created_at: string }
type Document = { id: string; filename: string; file_type: string; status: string; page_count?: number; file_size?: number; created_at: string }
type Question = { id: string; question_text: string; answer: string; analysis?: string; question_type: string; difficulty: string; options?: unknown[] | Record<string, unknown>; tags?: string[]; source_doc_id?: string; created_at: string }
type Entry = User | Document | Question
type List = { items: Entry[]; total: number; page: number; page_size: number }
type Edit = { kind: 'rename'; item: Document } | { kind: 'question'; item: Question }
const labels: Record<Kind, string> = { users: '用户', documents: '资料', questions: '题目' }
const difficulties: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }
const questionTypes: Record<string, string> = { single_choice: '单选题', multi_choice: '多选题', true_false: '判断题', fill_blank: '填空题', cloze: '完形填空', short_answer: '简答题', calculation: '计算题', formula: '公式题', coding: '编程题', material_analysis: '材料分析', term_definition: '名词解释' }
const documentStates: Record<string, { text: string; color?: string }> = { pending: { text: '待处理' }, parsing: { text: '解析中', color: 'processing' }, ready: { text: '可使用', color: 'success' }, error: { text: '处理失败', color: 'error' } }
const textStyle = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } as const
const byteSize = (bytes?: number) => bytes == null ? '—' : bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`

// A kind change remounts the resource so filters, modals and pending writes cannot cross sections.
export default function AdminBusiness({ kind }: { kind: Kind }) {
  return <BusinessResource key={kind} kind={kind} />
}

function BusinessResource({ kind }: { kind: Kind }) {
  const { message } = App.useApp()
  const currentUserId = useAuthStore(state => state.user?.id)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState({ search: '', page: 1, pageSize: 20 })
  const [revision, setRevision] = useState(0)
  const requestKey = JSON.stringify({ kind, ...query, revision })
  const [read, setRead] = useState<{ key: string; data?: List; error?: unknown; pending: boolean }>({ key: '', pending: true })
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [edit, setEdit] = useState<Edit | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [detailItem, setDetailItem] = useState<Entry | null>(null)
  const [detail, setDetail] = useState<{ data?: Document; error?: unknown; pending: boolean }>({ pending: false })
  const [detailRevision, setDetailRevision] = useState(0)
  const [form] = Form.useForm()
  const mounted = useRef(false)
  const mutations = useRef(new Set<AbortController>())
  const reload = () => setRevision(value => value + 1)
  const isCurrent = read.key === requestKey
  const pending = !isCurrent || read.pending
  const data = isCurrent ? read.data : undefined
  const error = isCurrent ? read.error : undefined

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; mutations.current.forEach(controller => controller.abort()); mutations.current.clear() }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setRead({ key: requestKey, pending: true })
    api.get<List>(`${consolePath}/${kind}`, {
      signal: controller.signal, timeout: 20000,
      params: { search: query.search, page: query.page, page_size: query.pageSize },
    }).then(response => {
      if (controller.signal.aborted) return
      // A list can shrink while it is open; move to its last valid server page.
      const lastPage = Math.max(1, Math.ceil(response.data.total / query.pageSize))
      if (query.page > lastPage) { setQuery(value => ({ ...value, page: lastPage })); return }
      setRead({ key: requestKey, data: response.data, pending: false })
    }).catch(reason => {
      if (!controller.signal.aborted) setRead({ key: requestKey, error: reason, pending: false })
    })
    return () => controller.abort()
  }, [requestKey, kind, query.search, query.page, query.pageSize])

  useEffect(() => {
    if (!detailItem || kind !== 'documents') return
    const controller = new AbortController()
    setDetail({ pending: true })
    api.get<Document>(`${consolePath}/documents/${encodeURIComponent(detailItem.id)}`, { signal: controller.signal, timeout: 20000 })
      .then(response => { if (!controller.signal.aborted) setDetail({ data: response.data, pending: false }) })
      .catch(reason => { if (!controller.signal.aborted) setDetail({ error: reason, pending: false }) })
    return () => controller.abort()
  }, [detailItem, kind, detailRevision])

  const changeUser = async (user: User) => {
    if (pendingUser || user.id === currentUserId) return
    const controller = new AbortController()
    mutations.current.add(controller); setPendingUser(user.id)
    try {
      await api.patch(`${consolePath}/users/${encodeURIComponent(user.id)}`, { is_active: !user.is_active }, { signal: controller.signal, timeout: 20000 })
      if (mounted.current && !controller.signal.aborted) { message.success(user.is_active ? '用户已停用' : '用户已启用'); reload() }
    } catch (reason) { if (mounted.current && !controller.signal.aborted) message.error(errorText(reason)) }
    finally { mutations.current.delete(controller); if (mounted.current) setPendingUser(null) }
  }

  const openEdit = (value: Edit) => {
    setSaveError(null); setEdit(value)
    form.resetFields()
    form.setFieldsValue(value.kind === 'rename' ? { filename: value.item.filename } : { question_text: value.item.question_text, answer: value.item.answer, analysis: value.item.analysis || '', difficulty: value.item.difficulty })
  }
  const save = async () => {
    if (!edit || saving) return
    const values = await form.validateFields().catch(() => null)
    if (!values || !mounted.current) return
    const controller = new AbortController()
    mutations.current.add(controller); setSaving(true); setSaveError(null)
    const payload = edit.kind === 'rename' ? { filename: values.filename.trim() } : {
      question_text: values.question_text.trim(), answer: values.answer.trim(), analysis: (values.analysis || '').trim(), difficulty: values.difficulty,
    }
    try {
      await api.patch(`${consolePath}/${kind}/${encodeURIComponent(edit.item.id)}`, payload, { signal: controller.signal, timeout: 20000 })
      if (mounted.current && !controller.signal.aborted) { message.success(edit.kind === 'rename' ? '资料名称已更新' : '题目已保存'); setEdit(null); reload() }
    } catch (reason) { if (mounted.current && !controller.signal.aborted) setSaveError(errorText(reason)) }
    finally { mutations.current.delete(controller); if (mounted.current) setSaving(false) }
  }

  const users: ColumnsType<User> = [
    { title: '用户', dataIndex: 'username', width: 190, render: (value, row) => <div><strong>{value}</strong>{row.id === currentUserId && <Tag style={{ marginLeft: 8 }}>当前账户</Tag>}<div><Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.email || '未填写邮箱'}</Typography.Text></div></div> },
    { title: '角色', width: 100, render: (_, row) => <Tag color={row.is_admin ? 'blue' : undefined}>{row.is_admin ? '管理员' : '学习用户'}</Tag> },
    { title: '账户状态', width: 110, render: (_, row) => <Tag color={row.is_active ? 'success' : 'default'}>{row.is_active ? '已启用' : '已停用'}</Tag> },
    { title: '注册时间', dataIndex: 'created_at', width: 175, render: localTime },
    { title: '操作', width: 100, render: (_, row) => <Popconfirm title={`确认${row.is_active ? '停用' : '启用'}用户「${row.username}」？`} description={row.is_active ? '停用后，该用户将无法继续访问系统。' : '启用后，该用户可以重新登录系统。'} okText={row.is_active ? '确认停用' : '确认启用'} cancelText="取消" disabled={row.id === currentUserId || pendingUser !== null || pending} onConfirm={() => changeUser(row)}><Button type="link" danger={row.is_active} disabled={row.id === currentUserId || pendingUser !== null || pending} loading={pendingUser === row.id}>{row.is_active ? '停用' : '启用'}</Button></Popconfirm> },
  ]
  const documents: ColumnsType<Document> = [
    { title: '资料名称', dataIndex: 'filename', width: 270, ellipsis: true, render: value => <strong title={value}>{value}</strong> },
    { title: '格式', dataIndex: 'file_type', width: 90, render: value => <Tag>{value?.toUpperCase() || '—'}</Tag> },
    { title: '处理状态', dataIndex: 'status', width: 115, render: value => <Tag color={documentStates[value]?.color}>{documentStates[value]?.text || value}</Tag> },
    { title: '页数', dataIndex: 'page_count', width: 80, render: value => value ?? '—' },
    { title: '大小', dataIndex: 'file_size', width: 100, render: byteSize },
    { title: '导入时间', dataIndex: 'created_at', width: 175, render: localTime },
    { title: '操作', width: 165, render: (_, row) => <Space size={0}><Button type="link" onClick={() => { setDetail({ pending: true }); setDetailItem(row) }}>详情</Button><Button type="link" onClick={() => openEdit({ kind: 'rename', item: row })}>重命名</Button></Space> },
  ]
  const questions: ColumnsType<Question> = [
    { title: '题干', dataIndex: 'question_text', width: 350, ellipsis: true, render: value => <span title={value}>{value}</span> },
    { title: '题型', dataIndex: 'question_type', width: 110, render: value => questionTypes[value] || value },
    { title: '难度', dataIndex: 'difficulty', width: 90, render: value => <Tag color={value === 'hard' ? 'orange' : value === 'easy' ? 'green' : 'blue'}>{difficulties[value] || value}</Tag> },
    { title: '标签', dataIndex: 'tags', width: 180, render: (tags: string[] | undefined) => tags?.length ? <span>{tags.slice(0, 3).map((tag, index) => <Tag key={`${tag}-${index}`}>{tag}</Tag>)}{tags.length > 3 && <Tag>+{tags.length - 3}</Tag>}</span> : '—' },
    { title: '操作', width: 140, render: (_, row) => <Space size={0}><Button type="link" onClick={() => setDetailItem(row)}>详情</Button><Button type="link" onClick={() => openEdit({ kind: 'question', item: row })}>编辑</Button></Space> },
  ]
  const documentDetail = detail.data
  const questionDetail = detailItem as Question | null
  const optionRows = questionDetail && kind === 'questions' ? Array.isArray(questionDetail.options) ? questionDetail.options : Object.entries(questionDetail.options || {}).map(([label, text]) => ({ label, text })) : []
  const displayOption = (option: unknown, index: number) => {
    if (typeof option === 'string') return `${String.fromCharCode(65 + index)}. ${option}`
    if (option && typeof option === 'object') { const value = option as { label?: unknown; text?: unknown }; return `${String(value.label || String.fromCharCode(65 + index))}. ${typeof value.text === 'string' ? value.text : JSON.stringify(option)}` }
    return String(option)
  }

  return <Card className="admin-business" styles={{ body: { padding: '20px', minWidth: 0 } }}>
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: 16 }}>
      <div><Typography.Title level={4} style={{ margin: 0 }}>{labels[kind]}管理</Typography.Title><Typography.Text type="secondary">{kind === 'users' ? '管理账户访问权限；当前管理员账户不可自行停用。' : kind === 'documents' ? '查看平台共享资料的处理情况与基本信息。' : '核对题干、答案和解析，维护平台共享题库。'}</Typography.Text></div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, maxWidth: '100%' }}><Input.Search type="search" aria-label={`搜索${labels[kind]}`} placeholder={kind === 'users' ? '搜索用户名或邮箱' : kind === 'documents' ? '搜索资料名称' : '搜索题干'} allowClear value={search} maxLength={200} style={{ flex: '1 1 200px', width: 260, minWidth: 0, maxWidth: '100%' }} onChange={event => { setSearch(event.target.value); if (!event.target.value) setQuery(value => ({ ...value, search: '', page: 1 })) }} onSearch={value => setQuery(current => ({ ...current, search: value.trim(), page: 1 }))} /><Button aria-label={`刷新${labels[kind]}列表`} icon={<ReloadOutlined />} onClick={reload} loading={pending}>刷新</Button></div>
    </div>
    {error ? <Alert type="error" showIcon message="数据加载失败" description={errorText(error)} action={<Button onClick={reload}>重试</Button>} /> : <Table<Entry> rowKey="id" dataSource={data?.items || []} loading={pending} columns={(kind === 'users' ? users : kind === 'documents' ? documents : questions) as ColumnsType<Entry>} scroll={{ x: kind === 'documents' ? 1000 : 850 }} locale={{ emptyText: pending ? '正在加载…' : <Empty description={query.search ? `没有匹配的${labels[kind]}` : `暂无${labels[kind]}记录`} /> }} pagination={{ current: query.page, pageSize: query.pageSize, total: data?.total || 0, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: total => `共 ${total} 条`, onChange: (page, pageSize) => setQuery(current => ({ ...current, page: pageSize === current.pageSize ? page : 1, pageSize })) }} />}
    <p className="admin-hint" style={{ marginBottom: 0 }}>仅管理员可使用此页面。{kind === 'documents' || kind === 'questions' ? '这里显示平台共享内容，修改后会影响学习端的对应内容。' : '账户权限由服务端校验，停用操作不会删除学习记录。'}</p>

    <Modal title={edit?.kind === 'rename' ? '重命名资料' : '编辑题目'} open={!!edit} onOk={() => void save()} onCancel={() => { if (!saving) setEdit(null) }} okText="保存" cancelText="取消" confirmLoading={saving} cancelButtonProps={{ disabled: saving }} closable={!saving} maskClosable={!saving} keyboard={!saving} width={edit?.kind === 'question' ? 720 : 520}>
      {saveError && <Alert type="error" showIcon message={saveError} style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical" disabled={saving} preserve={false}>
        {edit?.kind === 'rename' ? <Form.Item name="filename" label="资料名称" rules={[{ required: true, whitespace: true, message: '请输入资料名称' }, { max: 500, message: '资料名称最多 500 个字符' }]} extra="只修改显示名称；文件内容与格式保持原样。"><Input maxLength={500} /></Form.Item> : <>
          <Form.Item name="question_text" label="题干" rules={[{ required: true, whitespace: true, message: '请输入题干' }, { max: 100000, message: '题干最多 100000 个字符' }]}><Input.TextArea autoSize={{ minRows: 3, maxRows: 10 }} /></Form.Item>
          <Form.Item name="answer" label="答案" rules={[{ required: true, whitespace: true, message: '请输入答案' }, { max: 100000, message: '答案最多 100000 个字符' }]}><Input.TextArea autoSize={{ minRows: 2, maxRows: 8 }} /></Form.Item>
          <Form.Item name="analysis" label="解析" rules={[{ max: 100000, message: '解析最多 100000 个字符' }]}><Input.TextArea autoSize={{ minRows: 3, maxRows: 10 }} /></Form.Item>
          <Form.Item name="difficulty" label="难度" rules={[{ required: true, message: '请选择难度' }]}><Select options={Object.entries(difficulties).map(([value, label]) => ({ value, label })).concat(edit?.kind === 'question' && edit.item.difficulty && !difficulties[edit.item.difficulty] ? [{ value: edit.item.difficulty, label: edit.item.difficulty }] : [])} /></Form.Item>
          <Typography.Text type="secondary">题型、选项与来源可在详情中核对。</Typography.Text>
        </>}
      </Form>
    </Modal>

    <Modal title={kind === 'documents' ? '资料详情' : '题目详情'} open={!!detailItem} onCancel={() => setDetailItem(null)} footer={<Button onClick={() => setDetailItem(null)}>关闭</Button>} width={720}>
      {kind === 'documents' ? detail.error ? <Alert type="error" showIcon message="详情加载失败" description={errorText(detail.error)} action={<Button onClick={() => setDetailRevision(value => value + 1)}>重试</Button>} /> : detail.pending ? <Skeleton active /> : documentDetail && <Descriptions column={1} bordered size="small" items={[
        { key: 'filename', label: '资料名称', children: <span style={textStyle}>{documentDetail.filename}</span> },
        { key: 'id', label: '资料编号', children: <Typography.Text copyable>{documentDetail.id}</Typography.Text> },
        { key: 'format', label: '文件格式', children: documentDetail.file_type?.toUpperCase() },
        { key: 'status', label: '处理状态', children: documentStates[documentDetail.status]?.text || documentDetail.status },
        { key: 'pages', label: '页数', children: documentDetail.page_count ?? '—' },
        { key: 'size', label: '文件大小', children: byteSize(documentDetail.file_size) },
        { key: 'time', label: '导入时间', children: localTime(documentDetail.created_at) },
      ]} /> : questionDetail && <div style={textStyle}>
        <Space wrap><Tag>{questionTypes[questionDetail.question_type] || questionDetail.question_type}</Tag><Tag>{difficulties[questionDetail.difficulty] || questionDetail.difficulty}</Tag>{questionDetail.tags?.map((tag, index) => <Tag key={`${tag}-${index}`}>{tag}</Tag>)}</Space>
        <Typography.Title level={5}>题干</Typography.Title><p>{questionDetail.question_text}</p>
        {optionRows.length > 0 && <><Typography.Title level={5}>选项</Typography.Title>{optionRows.map((option, index) => <p key={index}>{displayOption(option, index)}</p>)}</>}
        <Typography.Title level={5}>答案</Typography.Title><p>{questionDetail.answer || '未填写'}</p>
        <Typography.Title level={5}>解析</Typography.Title><p>{questionDetail.analysis || '暂无解析'}</p>
        <Typography.Text type="secondary">题目编号：{questionDetail.id}<br />来源资料：{questionDetail.source_doc_id || '未关联资料'}<br />创建时间：{localTime(questionDetail.created_at)}</Typography.Text>
      </div>}
    </Modal>
  </Card>
}
