export const user = { id: 'shell-demo', username: 'preview', email: 'preview@example.test' }
export const doc = { id: 'shell-doc', filename: '期末复习讲义.md', file_type: 'md', status: 'ready', subject: '生物', material_kind: 'knowledge' }
export const initialPlans = [{ id: 'plan-a', name: '期末复习 · 细胞生物学', status: 'active', progress: 25, description: '每天整理一个章节', target_end_date: '2026-10-20' }]
export async function mockShell(page) {
  const state = { calls: [], plans: structuredClone(initialPlans), nickname: '小林', faults: {}, read: false, cardDone: false, extraGoal: null, feedMany: false, delays: {}, goalDone: false, longSchedule: false, lostToggle: false }
  await page.addInitScript(value => {
    localStorage.setItem('knowall_token', 'local-shell-preview')
    localStorage.setItem('knowall_user', JSON.stringify(value))
  }, user)
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace('/api/v1', ''), method = request.method()
    const body = request.postDataBuffer()
    state.calls.push({ path, method, params: Object.fromEntries(url.searchParams), body: body?.toString() || '', contentType: request.headers()['content-type'] })
    if (state.delays[path]) await new Promise(resolve => setTimeout(resolve, state.delays[path]))
    if (path.endsWith('/toggle') && state.lostToggle) {
      state.lostToggle = false; state.goalDone = !state.goalDone
      state.plans[0].progress = state.goalDone ? 100 : 0
      return route.fulfill({ status: 503, json: { detail: '保存成功但响应丢失' } })
    }
    if (state.faults[path]) return route.fulfill({ status: 503, json: { detail: '隔离测试故障' } })
    let value = []
    if (path === '/documents/') value = [doc]
    else if (path.endsWith('/chunks')) value = [{ id: 'shell-c', index: 0, text: '第一章 细胞\n细胞是生命活动的基本单位。', page_range: '1' }]
    else if (path === '/documents/shell-doc') value = doc
    else if (path === '/documents/upload' || path === '/documents/import-url') value = { document_id: doc.id, status: 'ready' }
    else if (path.endsWith('/organization')) {
      const input = request.postDataJSON()
      if (!input || typeof input.subject !== 'string' || !['knowledge', 'question_bank', 'mixed'].includes(input.material_kind)) return route.fulfill({ status: 422, json: { detail: '分类需要JSON请求体' } })
      value = { ...doc, ...input }
    }
    else if (path === '/memory/stats') value = { due_today: state.cardDone ? 0 : 1, reviewed_today: state.cardDone ? 1 : 0, total_cards: 12, total_reviews: 8, average_accuracy: 0.8, weak_points_count: 2, review_queue_count: 1, decay_count: 0 }
    else if (path === '/flashcards/due') value = { due_count: state.cardDone ? 0 : 1, cards: state.cardDone ? [] : [{ id: 'card-a', front: '细胞膜的主要作用是什么？', back: '调节物质交换，接收外界信号。', card_type: 'qa' }] }
    else if (path === '/flashcards/review') { state.cardDone = true; value = { card_id: 'card-a', next_review_at: '2026-10-15' } }
    else if (path === '/study/plans' && method === 'GET') value = state.plans
    else if (path === '/study/plans' && method === 'POST') { const input = request.postDataJSON(); const plan = { id: 'plan-new', name: input.name, description: input.description, status: 'active', progress: 0 }; state.plans.unshift(plan); value = { ...plan, plan_id: plan.id } }
    else if (path.startsWith('/study/plans/') && method === 'DELETE') { state.plans = state.plans.filter(p => p.id !== path.split('/').at(-1)); value = { status: 'deleted' } }
    else if (path.startsWith('/study/plans/')) value = { ...state.plans.find(p => p.id === path.split('/').at(-1)), goals: [{ id: 'goal-a', title: '复习第一章', completed: state.goalDone || false }, ...(state.extraGoal ? [state.extraGoal] : [])], short_term_plan: Array.from({ length: state.longSchedule ? 10 : 1 }, (_, i) => ({ day: i + 1, date: '2026-10-' + (12 + i), hours: [{ hour: '19:00', topic: i === 9 ? '最后一章核对' : '梳理细胞结构', is_review: false }] })), long_term_plan: state.longSchedule ? [{ week: 1, days: [{ day: 1, date: '2026-10-20', topics: ['长期巩固任务'], hours: 1, is_review_day: true }] }, { week: 2, days: [{ day: 8, date: '2026-10-27', topics: ['第二周回顾任务'], hours: 1, is_review_day: true }] }] : [] }
    else if (path === '/study/goals') { const input = request.postDataJSON(); state.extraGoal = { id: 'goal-new', title: input.title, completed: false }; value = { goal_id: 'goal-new' } }
    else if (path.endsWith('/toggle')) { state.goalDone = !state.goalDone; state.plans[0].progress = state.goalDone ? 100 : 0; value = { goal_id: 'goal-a', completed: state.goalDone, plan_progress: state.plans[0].progress } }
    else if (path === '/study/reminders/due') value = { count: 1, reminders: [{ id: 'r1', message: '整理细胞结构笔记', remind_at: '2026-10-12T10:00:00', plan_id: 'plan-a' }] }
    else if (path === '/user/profile') { if (method === 'PUT') state.nickname = request.postDataJSON().nickname; value = { ...user, nickname: state.nickname, phone: '', avatar_url: '', is_active: true, created_at: '2026-09-01' } }
    else if (path === '/notifications') value = state.feedMany ? { total: 6, unread_count: state.read ? 0 : 6, items: Array.from({ length: Number(url.searchParams.get('page')) === 2 ? 1 : 5 }, (_, i) => ({ id: 'n' + (Number(url.searchParams.get('page')) === 2 ? 6 : i), title: Number(url.searchParams.get('page')) === 2 ? '第六条通知' : '复习提醒 ' + i, content: '今天可以回顾细胞膜的结构与功能。', is_read: state.read })) } : { total: 1, unread_count: state.read ? 0 : 1, items: [{ id: 'n1', title: '复习提醒', content: '今天可以回顾细胞膜的结构与功能。', category: 'study', is_read: state.read, created_at: '2026-10-12T10:00:00Z' }] }
    else if (path === '/notifications/read-all') { state.read = true; value = { count: 1 } }
    else if (path === '/user/history') value = state.feedMany ? { total: 6, items: Array.from({ length: Number(url.searchParams.get('page')) === 2 ? 1 : 5 }, (_, i) => ({ id: 'h' + (Number(url.searchParams.get('page')) === 2 ? 6 : i), action_label: Number(url.searchParams.get('page')) === 2 ? '第六条活动' : '完成练习 ' + i, created_at: '2026-10-12T09:00:00Z' })) } : { total: 1, items: [{ id: 'h1', action_label: '完成细胞结构练习', action_type: 'quiz', created_at: '2026-10-12T09:00:00Z' }] }
    else if (path === '/review/mastery') value = { overall_mastery: 0.72, total_knowledge_points: 11, strong_count: 8, weak_count: 3 }
    else if (path === '/user/password') value = { success: true }
    else if (/generate|recommend/.test(path) && method !== 'GET') return route.fulfill({ status: 403, json: { detail: '真实AI调用禁止' } })
    return route.fulfill({ json: value })
  })
  return state
}
