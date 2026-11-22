import { useMemo } from 'react'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkMath from 'remark-math'
import { visit, EXIT } from 'unist-util-visit'
import LearningContent from './LearningContent'

const parser = unified().use(remarkParse).use(remarkMath)

/** Native math nodes exclude escaped dollars and code; ordinary fronts stay literal. */
export default function FlashcardFront({ text }: { text: string }) {
  const mathematical = useMemo(() => {
    let found = false
    visit(parser.parse(text), ['math', 'inlineMath'], () => { found = true; return EXIT })
    return found
  }, [text])
  return <div className="flashcard-front">
    {mathematical ? <LearningContent text={text} /> : <span style={{ whiteSpace: 'pre-wrap' }}>{text}</span>}
  </div>
}
