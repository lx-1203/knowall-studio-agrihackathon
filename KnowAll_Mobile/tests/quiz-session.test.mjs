import test from 'node:test'
import assert from 'node:assert/strict'
import { decodeQuizSession } from '../src/quizSession.ts'
const session = { version: 1, accountId: 'alice', exam: { paper_id: 'p1', title: '练习', questions: [{ id: 'q1', question_type: 'true_false', question_text: 'DNA保存遗传信息。' }] }, answers: { q1: 'true' }, index: 0, result: null }
test('saved answers and cursor survive a roundtrip', () => assert.deepEqual(decodeQuizSession(JSON.stringify(session), 'alice'), session))
test('a different account cannot restore the draft', () => assert.throws(() => decodeQuizSession(JSON.stringify(session), 'bob')))
test('malformed JSON remains an error rather than becoming an empty draft', () => assert.throws(() => decodeQuizSession('{broken', 'alice')))
test('cursor outside the paper is rejected', () => assert.throws(() => decodeQuizSession(JSON.stringify({ ...session, index: 1 }), 'alice')))
test('unknown question answers are rejected', () => assert.throws(() => decodeQuizSession(JSON.stringify({ ...session, answers: { other: 'A' } }), 'alice')))
test('duplicate question IDs are rejected', () => assert.throws(() => decodeQuizSession(JSON.stringify({ ...session, exam: { ...session.exam, questions: [...session.exam.questions, ...session.exam.questions] } }), 'alice')))
