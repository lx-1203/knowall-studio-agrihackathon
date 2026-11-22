export const content = '原生学习 $x^2$，金额 \\$5 和 \\$10。\n\n$$\n\\frac{1}{2}\n$$\n\n```python\ndef learn(value):\n    return value + 1\n```\n\n`$raw$`\n\n|知识|进度|\n|---|---|\n|数学|50%|'
export const feedback = '解释 $y^2$\n\n```js\nconst score = 0.5;\n```'
export const chunks = [{ id: 'content-c1', index: 0, page_range: 'p1', text: '# 第一章 原生学习\n\n'+content }, { id: 'content-c2', index: 1, page_range: 'p2', text: '# 第二章 当前来源\n\n当前第二段原文' }]
export const source = { chunk_ids: ['content-c1'], page_ranges: ['p1'] }
export const question = { id: 'content-q', question_type: 'short_answer', question_text: content, options: [] }
export const card = { id: 'content-card', front: content, back: feedback }
export const pack = { id: 'content-pack', stale: false, sections: [{ id: 'content-section', title: '学习章节', notes: content, chunk_ids: ['content-c1'], flashcards: [card] }] }
export const session = { items: [{ kind: 'question', reason: 'practice', title: '章节笔记', notes: content, question, source }, { kind: 'card', reason: 'due', title: '卡片笔记', notes: '回忆之后评分', card, source }] }
export const result = { is_correct: false, correct_answer: '1;2', score_ratio: 0.5, analysis: feedback, source }
