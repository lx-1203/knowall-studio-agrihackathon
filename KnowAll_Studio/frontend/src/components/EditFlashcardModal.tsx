import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Form, Input, Modal, Space, Spin } from 'antd'
import { isAxiosError } from 'axios'
import { editFlashcardContent, getFlashcardContent, type CardContentValues, type FlashcardContent } from '../flashcardEditApi'
import LoadFailure from './LoadFailure'
import FlashcardDraftPreview from './FlashcardDraftPreview'

export default function EditFlashcardModal({ cardId, onClose, onSaved }: {
  cardId: string; onClose: () => void; onSaved: (card: FlashcardContent) => void
}) {
  const [form] = Form.useForm<CardContentValues>()
  const { message } = App.useApp()
  const controller = useRef<AbortController | null>(null)
  const pendingRef = useRef(false)
  const [card, setCard] = useState<FlashcardContent | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const load = async (request: AbortController) => {
    setLoading(true); setError(null)
    try {
      const result = await getFlashcardContent(cardId, { signal: request.signal })
      if (controller.current === request && !request.signal.aborted) setCard(result)
    } catch (failure) {
      if (controller.current === request && !request.signal.aborted) setError(failure)
    } finally {
      if (controller.current === request && !request.signal.aborted) setLoading(false)
    }
  }
  useEffect(() => {
    const request = new AbortController(); controller.current = request
    load(request)
    return () => { request.abort(); if (controller.current === request) controller.current = null }
  }, [cardId])
  const status = isAxiosError(error) ? error.response?.status : undefined
  const conflict = status === 409
  const supported = card && ['qa', 'cloze', 'compare'].includes(card.card_type)
  const save = async (values: CardContentValues) => {
    const request = controller.current
    if (!card || !request || request.signal.aborted || pendingRef.current || conflict) return
    pendingRef.current = true; setPending(true); setError(null)
    try {
      const result = await editFlashcardContent(cardId, { ...values, hints: values.hints || '', original: {
        card_type: card.card_type, front: card.front, back: card.back, hints: card.hints || '',
      } }, { signal: request.signal })
      if (controller.current === request && !request.signal.aborted) onSaved(result)
    } catch (failure) {
      if (controller.current === request && !request.signal.aborted) setError(failure)
    } finally {
      pendingRef.current = false
      if (controller.current === request && !request.signal.aborted) setPending(false)
    }
  }
  const close = () => {
    if (pendingRef.current) message.info('已停止等待；若服务端已保存，请重新打开卡片确认结果。')
    controller.current?.abort(); onClose()
  }
  return <Modal open title="编辑卡片内容" onCancel={close} destroyOnHidden
    footer={<Space><Button onClick={close}>取消</Button><Button type="primary" loading={pending}
      disabled={loading || !supported || pending || conflict} onClick={() => form.submit()}>保存修改</Button></Space>}>
    {loading && <Spin />}
    {!loading && !card && Boolean(error) && <LoadFailure title="卡片内容加载失败" error={error}
      pending={loading} onRetry={() => { if (controller.current) load(controller.current) }} />}
    {card && !supported && <Alert type="warning" showIcon message="此类型暂不支持编辑" />}
    {card && supported && <>
      <p>修改内容后保留复习进度及下次复习安排。卡片类型：{({ qa: '问答卡', cloze: '填空卡', compare: '对比卡' } as Record<string, string>)[card.card_type]}</p>
      {conflict ? <Alert type="warning" showIcon message="卡片已在其他地方更新" description="草稿仍保留在这里。请先复制需要的内容，关闭并重新打开卡片查看最新版本，再决定修改。" />
        : status === 422 ? <Alert type="error" showIcon message="卡片内容无效" description="请检查正反面；填空卡需要保留 {{c1::答案}} 标记。" />
          : Boolean(error) && <LoadFailure inline title="保存修改失败" error={error} retryLabel="重新保存" pending={pending} onRetry={() => form.submit()} />}
      <Form form={form} layout="vertical" preserve={false} disabled={pending} onFinish={save}
        initialValues={{ front: card.front, back: card.back, hints: card.hints || '' }}>
        <Form.Item name="front" label="问题 / 正面" rules={[
          { required: true, whitespace: true, message: '请输入问题或正面内容' },
          { min: 4, message: '正面至少需要4个字符' }, { max: 20000, message: '正面最多20000个字符' },
        ]}><Input.TextArea rows={3} /></Form.Item>
        <Form.Item name="back" label="答案 / 解释" rules={[
          { required: true, whitespace: true, message: '请输入答案或解释' }, { max: 20000, message: '答案最多20000个字符' },
        ]}><Input.TextArea rows={5} /></Form.Item>
        <Form.Item name="hints" label="提示（可选）" rules={[{ max: 1000, message: '提示最多1000个字符' }]}><Input.TextArea rows={2} /></Form.Item>
      </Form>
      <FlashcardDraftPreview form={form} cardType={card.card_type} />
    </>}
  </Modal>
}
