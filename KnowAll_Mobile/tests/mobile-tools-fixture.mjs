export const user = { id: 'tools-demo', username: '小林', email: 'tools@example.test' }
export const questions = [
  { id: 'q1', question_type: 'single_choice', question_text: '细胞核的主要作用是什么？', options: [{ label: 'A', text: '储存遗传信息' }, { label: 'B', text: '提供能量' }], answer: 'A', difficulty: 'easy', cognitive_level: 'L1_remember' },
  { id: 'q2', question_type: 'true_false', question_text: 'DNA 可以保存遗传信息。', options: [], answer: 'true', difficulty: 'easy', cognitive_level: 'L1_remember' },
  { id: 'q3', question_type: 'multi_choice', question_text: '哪些属于遗传物质？', options: [{ label: 'A', text: 'DNA' }, { label: 'B', text: 'RNA' }, { label: 'C', text: '脂质' }], answer: 'A,B', difficulty: 'medium', cognitive_level: 'L1_remember' },
]
export const passage = { id: 'p1', title: '细胞结构', content: '细胞核储存遗传信息。', category: 'custom', difficulty: 1, tags: [] }
const cards = [{ id: 'c1', front: '细胞膜的作用？', back: '调节物质交换。', deck: 'd1' }, { id: 'c2', front: 'DNA 的作用？', back: '保存遗传信息。', deck: 'd2' }]
export async function mockTools(page) {
  const state = { calls: [], faults: {}, delays: {}, rated: [], passages: [passage], messages: [], reviewed: false }
  await page.addInitScript(value => { localStorage.setItem('knowall_token', 'isolated-tools'); localStorage.setItem('knowall_user', JSON.stringify(value)) }, user)
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/v1', ''), method = req.method()
    state.calls.push({ path, method, params: Object.fromEntries(url.searchParams), body: req.postData() })
    if (state.delays[path]) await new Promise(resolve => setTimeout(resolve, state.delays[path]))
    if (state.faults[path]) return route.fulfill({ status: 503, json: { detail: '隔离测试故障' } })
    let data
    if (path === '/quiz/questions') data = questions
    else if (path === '/quiz/exam/create') { const input = req.postDataJSON(); const selected = questions.filter(q => input.question_ids.includes(q.id)); data = { paper_id: 'paper1', title: '本次练习', question_count: selected.length, questions: selected, total_score: 5 * selected.length } }
    else if (path === '/quiz/exam/submit') { const input = req.postDataJSON(); if (Array.isArray(input.answers)) return route.fulfill({ status: 422, json: { detail: '答案必须按题目ID提供字典' } }); data = { total: questions.length, correct: 3, percentage: 100, score: 15, details: questions.map(q => ({ question_id: q.id, user_answer: input.answers[q.id] || '', correct_answer: q.answer, is_correct: true, analysis: '已根据原文核对。' })) } }
    else if (path === '/flashcards/decks') data = [{ id: 'd1', name: '细胞生物学', card_count: 1 }, { id: 'd2', name: '遗传学', card_count: 1 }]
    else if (path === '/flashcards/due') { const selected = cards.filter(c => !state.rated.includes(c.id) && (!url.searchParams.get('deck_id') || c.deck === url.searchParams.get('deck_id'))).map(({ deck, ...c }) => c); data = { due_count: selected.length, cards: selected } }
    else if (path === '/flashcards/review') { const input = req.postDataJSON(); state.rated.push(input.card_id); data = { card_id: input.card_id, next_review_at: '2026-10-20', review_count: 1 } }
    else if (path === '/recitation/passages' && method === 'GET') data = { passages: state.passages, total: state.passages.length }
    else if (path === '/recitation/passages' && method === 'POST') { const input = req.postDataJSON(); data = { ...input, id: 'p-new', category: 'custom' }; state.passages.push(data) }
    else if (path.startsWith('/recitation/passages/') && method === 'DELETE') { state.passages = state.passages.filter(p => p.id !== path.split('/').at(-1)); data = { deleted: true } }
    else if (path === '/recitation/hide-plan') data = { segments: [{ text: '细胞核', hidden: true }, { text: '储存遗传信息。', hidden: false }], hide_ratio: 0.3 }
    else if (path === '/recitation/dictation/submit') data = { record_id: 'r1', accuracy: 0.8, diffs: [{ type: 'equal', text: '细胞核' }, { type: 'delete', text: '储存' }, { type: 'insert', text: '保存' }, { type: 'equal', text: '遗传信息。' }], correct_chars: 8, missing_chars: 2, extra_chars: 2, original_length: 10, input_length: 10 }
    else if (path === '/recitation/review') { state.reviewed = true; data = { record_id: 'r1', next_review_at: '2026-10-20', review_count: 1 } }
    else if (path === '/recitation/due') data = { due_items: [], total: 0 }
    else if (path === '/recitation/stats') data = { total_passages: 1, total_records: 0, avg_accuracy: 0, mastered: 0 }
    else if (path === '/chat/conversations') data = [{ id: 'conv1', title: '细胞结构讨论' }, { id: 'conv2', title: '遗传学讨论' }]
    else if (path.startsWith('/chat/conversations/')) data = { conversation_id: path.split('/').at(-1), messages: [{ id: 'm1', role: 'assistant', content: path.endsWith('conv1') ? '细胞核保存遗传信息。' : 'DNA 是遗传物质。' }] }
    else if (path === '/chat/assistant') { const input = req.postDataJSON(); state.messages.push(input.message); data = { conversation_id: input.conversation_id || 'conv-new', message: '这是隔离测试回复：细胞核保存遗传信息。' } }
    else if (/generate|recommend/.test(path)) return route.fulfill({ status: 403, json: { detail: '禁止真实AI' } })
    else data = []
    return route.fulfill({ json: data })
  })
  return state
}
