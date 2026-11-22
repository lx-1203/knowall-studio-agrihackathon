import { useEffect, useRef, useState } from 'react'
import { App, Button, Form, Input, Modal, Space } from 'antd'
import { createFlashcardDeck, type ManualDeck, type ManualDeckInput } from '../flashcardDeckApi'
import LoadFailure from './LoadFailure'

export default function CreateFlashcardDeckModal({ onClose, onCreated }: {
  onClose: () => void; onCreated: (deck: ManualDeck) => void
}) {
  const [form] = Form.useForm<ManualDeckInput>()
  const { message } = App.useApp()
  const controller = useRef<AbortController | null>(null)
  const pendingRef = useRef(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    const request = new AbortController()
    controller.current = request
    return () => { request.abort(); if (controller.current === request) controller.current = null }
  }, [])
  const save = async (values: ManualDeckInput) => {
    const request = controller.current
    if (!request || request.signal.aborted || pendingRef.current) return
    pendingRef.current = true; setPending(true); setError(null)
    try {
      const deck = await createFlashcardDeck(values, { signal: request.signal })
      if (controller.current === request && !request.signal.aborted) onCreated(deck)
    } catch (failure) {
      if (controller.current === request && !request.signal.aborted) setError(failure)
    } finally {
      pendingRef.current = false
      if (controller.current === request && !request.signal.aborted) setPending(false)
    }
  }
  const close = () => {
    if (pendingRef.current) message.info('已停止等待；若服务端已收到请求，请刷新牌组确认创建结果。')
    controller.current?.abort(); onClose()
  }
  return <Modal open title="创建牌组" onCancel={close} destroyOnHidden
    footer={<Space><Button onClick={close}>取消</Button><Button type="primary" loading={pending} disabled={pending} onClick={() => form.submit()}>创建并添加卡片</Button></Space>}>
    <p>先创建你的牌组，再添加第一张卡片。</p>
    {Boolean(error) && <LoadFailure inline title="创建牌组失败" error={error} retryLabel="重新创建" pending={pending} onRetry={() => form.submit()} />}
    <Form form={form} layout="vertical" preserve={false} disabled={pending} onFinish={save}
      initialValues={{ name: '', description: '' }}>
      <Form.Item name="name" label="牌组名称" rules={[
        { required: true, whitespace: true, message: '请输入牌组名称' }, { max: 200, message: '名称最多200个字符' },
      ]}><Input /></Form.Item>
      <Form.Item name="description" label="说明（可选）" rules={[{ max: 2000, message: '说明最多2000个字符' }]}><Input.TextArea rows={3} /></Form.Item>
    </Form>
  </Modal>
}
