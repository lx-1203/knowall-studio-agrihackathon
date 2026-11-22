import type { MaterialQuestion } from './materialLearning'
import type { ExamResult } from './types'
export type QuizQuestion = MaterialQuestion & { difficulty?: string; blanks?: unknown[] }
export type QuizExam = { paper_id: string; title: string; questions: QuizQuestion[] }
export type QuizSession = { version: 1; accountId: string; exam: QuizExam; answers: Record<string, string>; index: number; result: ExamResult | null }
export function decodeQuizSession(raw: string, accountId: string): QuizSession {
  const value = JSON.parse(raw)
  const questions = value?.exam?.questions
  if (value?.version !== 1 || value.accountId !== accountId || typeof value?.exam?.paper_id !== 'string' || !value.exam.paper_id || !Array.isArray(questions) || !questions.length ||
    questions.some(q => !q || typeof q.id !== 'string' || !q.id || typeof q.question_text !== 'string' || typeof q.question_type !== 'string') ||
    new Set(questions.map(q => q.id)).size !== questions.length || !Number.isSafeInteger(value.index) || value.index < 0 || value.index >= questions.length ||
    !value.answers || typeof value.answers !== 'object' || Array.isArray(value.answers) ||
    Object.entries(value.answers).some(([id, answer]) => !questions.some(q => q.id === id) || typeof answer !== 'string') ||
    value.result !== null && (!Array.isArray(value.result?.details) || typeof value.result.total !== 'number')) throw new Error('练习草稿不完整')
  return value
}
