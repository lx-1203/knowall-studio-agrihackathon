import LearningContent from './LearningContent'

interface CompareTableProps { back: string }

// Share the native GFM renderer with other learning content. It preserves
// empty cells, escaped pipes and surrounding paragraphs/code without a parser.
export default function CompareTable({ back }: CompareTableProps) {
  return <div className="flashcard-answer"><LearningContent text={back} /></div>
}
