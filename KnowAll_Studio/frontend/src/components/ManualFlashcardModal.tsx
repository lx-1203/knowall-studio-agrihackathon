import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Form, Input, Modal, Select, Space } from 'antd'
import { isAxiosError } from 'axios'
import { createFlashcard, type ManualFlashcardInput } from '../api'
import LoadFailure from './LoadFailure'
import FlashcardDraftPreview from './FlashcardDraftPreview'

type Values = Pick<ManualFlashcardInput, 'card_type' | 'front' | 'back' | 'hints'>

export default function ManualFlashcardModal({ deck, onClose, onSaved }: {
  deck: { id: string; name: string }; onClose: () => void; onSaved: () => void
}) {
  const [form] = Form.useForm<Values>()
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
  const save = async (values: Values) => {
    const request = controller.current
    if (!request || request.signal.aborted || pendingRef.current) return
    pendingRef.current = true; setPending(true); setError(null)
    try {
      await createFlashcard({ ...values, hints: values.hints || '', deck_id: deck.id, tags: [] }, { signal: request.signal })
      if (controller.current === request && !request.signal.aborted) onSaved()
    } catch (failure) {
      if (controller.current === request && !request.signal.aborted) setError(failure)
    } finally {
      pendingRef.current = false
      if (controller.current === request && !request.signal.aborted) setPending(false)
    }
  }
  const close = () => {
    if (pendingRef.current) message.info('已停止等待；若服务端已收到请求，请刷新牌组确认保存结果。')
    controller.current?.abort(); onClose()
  }
  const invalid = isAxiosError(error) && error.response?.status === 422
  return <Modal open title="手动添加卡片" onCancel={close} destroyOnHidden
    footer={<Space><Button onClick={close}>取消</Button><Button type="primary" loading={pending} disabled={pending} onClick={() => form.submit()}>保存卡片</Button></Space>}>
    <p>添加到：{deck.name}</p>
    {invalid ? <Alert type="error" showIcon message="卡片内容无效" description="请检查正反面内容及卡片类型；填空卡需要包含 {{c1::答案}} 标记。" />
      : Boolean(error) && <LoadFailure inline title="保存卡片失败" error={error} retryLabel="重新保存" pending={pending} onRetry={() => form.submit()} />}
    <Form form={form} layout="vertical" preserve={false} disabled={pending} onFinish={save}
      initialValues={{ card_type: 'qa', front: '', back: '', hints: '' }}>
      <Form.Item name="card_type" label="卡片类型"><Select options={[
        { value: 'qa', label: '问答卡' }, { value: 'cloze', label: '填空卡' }, { value: 'compare', label: '对比卡' },
      ]} /></Form.Item>
      <Form.Item name="front" label="问题 / 正面" rules={[
        { required: true, whitespace: true, message: '请输入问题或正面内容' },
        { min: 4, message: '正面至少需要4个字符' }, { max: 20000, message: '正面最多20000个字符' },
      ]}><Input.TextArea rows={3} /></Form.Item>
      <Form.Item name="back" label="答案 / 解释" rules={[
        { required: true, whitespace: true, message: '请输入答案或解释' }, { max: 20000, message: '答案最多20000个字符' },
      ]}><Input.TextArea rows={5} /></Form.Item>
      <Form.Item name="hints" label="提示（可选）" rules={[{ max: 1000, message: '提示最多1000个字符' }]}><Input.TextArea rows={2} /></Form.Item>
    </Form>
    <FlashcardDraftPreview form={form} />
  </Modal>
}
