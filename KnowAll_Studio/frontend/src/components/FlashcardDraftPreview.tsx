import { Form, type FormInstance } from 'antd'
import FlashcardFront from './FlashcardFront'
import ClozeRenderer from './ClozeRenderer'
import LearningContent from './LearningContent'

/** AntD observes the actual draft; preview never writes or changes review state. */
export default function FlashcardDraftPreview({ form, cardType }: { form: FormInstance; cardType?: string }) {
  const front = Form.useWatch('front', form) || ''
  const back = Form.useWatch('back', form) || ''
  const hints = Form.useWatch('hints', form) || ''
  const selectedType = Form.useWatch('card_type', form)
  return <details className="flashcard-draft-preview" style={{ marginTop: 12 }}>
    <summary style={{ cursor: 'pointer' }}>卡片预览</summary>
    <div style={{ padding: '12px 0' }}>
      {(cardType || selectedType) === 'cloze'
        ? <ClozeRenderer key={JSON.stringify([front, back, hints])} front={front} back={back} hints={hints} />
        : <><FlashcardFront text={front} />{hints && <div style={{ marginTop: 8 }}>提示：<FlashcardFront text={hints} /></div>}</>}
      <details style={{ marginTop: 12 }}><summary style={{ cursor: 'pointer' }}>答案 / 解释预览</summary>
        <div className="flashcard-answer"><LearningContent text={back} /></div>
      </details>
    </div>
  </details>
}
