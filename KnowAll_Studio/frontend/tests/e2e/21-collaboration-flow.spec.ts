import { test, expect, stubBackend, E2E_USER } from './fixtures'
import type { Page, WebSocketRoute } from '@playwright/test'

test('host pushes a question to another client and both see the score and room end', async ({ page, context }) => {
  const student = await context.newPage()
  await student.addInitScript(user => {
    localStorage.setItem('knowall_token', 'student-test-token')
    localStorage.setItem('knowall_user', JSON.stringify(user))
  }, { ...E2E_USER, id: 'student', username: '学生甲' })
  await stubBackend(student)
  const sockets: WebSocketRoute[] = []
  const question = { live_question_id: 'live-q', question_id: 'q1', question_index: 1, question_type: 'single_choice', question_text: '哪种细胞器提供能量？', options: [{ label: 'A', text: '线粒体' }, { label: 'B', text: '细胞壁' }], mode: 'broadcast' }
  const room = { session_id: 'room1', room_code: '123456', title: '生物练习', host_id: E2E_USER.id, participants: [] }
  let pushed = false
  let score = 0
  async function configure(client: Page) {
    await client.routeWebSocket(/\/ws\/sync/, socket => {
      sockets.push(socket)
      socket.onMessage(message => {
        const data = JSON.parse(String(message))
        if (data.type.startsWith('live_')) sockets.filter(peer => peer !== socket).forEach(peer => peer.send(String(message)))
      })
    })
    await client.route('**/api/v1/flashcards/search**', route => route.fulfill({ json: { results: [{ id: 'q1', question_type: 'single_choice', question_text: question.question_text }] } }))
    await client.route('**/api/v1/live/**', route => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/join')) return route.fulfill({ json: { participant_id: 'student1' } })
      if (path.endsWith('/questions')) return route.fulfill({ json: { questions: pushed ? [question] : [] } })
      if (path.endsWith('/push')) { pushed = true; return route.fulfill({ json: { pushed_count: 1, questions: [question] } }) }
      if (path.endsWith('/answer')) {
        expect(route.request().postDataJSON()).toMatchObject({ live_question_id: question.live_question_id, user_answer: 'A' })
        score = 10
        return route.fulfill({ json: { is_correct: true, correct_answer: 'A', score } })
      }
      if (path.endsWith('/leaderboard')) return route.fulfill({ json: { leaderboard: [{ rank: 1, user_name: '学生甲', score, correct_count: score ? 1 : 0, total_answered: score ? 1 : 0, accuracy: score ? 1 : 0 }] } })
      if (path.endsWith('/end')) return route.fulfill({ json: { ended: true } })
      return route.fulfill({ json: room })
    })
  }
  await configure(page)
  await configure(student)
  await page.goto('/live')
  await page.getByPlaceholder('会话标题').fill(room.title)
  await page.getByRole('button', { name: /创\s*建$/ }).click()
  await expect(page.getByText('123456', { exact: true })).toBeVisible()
  await student.goto('/live')
  await student.getByPlaceholder('6位房间码').fill(room.room_code)
  await student.getByPlaceholder('你的昵称（可选）').fill('学生甲')
  await student.getByRole('button', { name: /加\s*入$/ }).click()
  await expect(student.getByText('已加入会话', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /推送考题/ }).click()
  await page.getByPlaceholder('搜索题目...').fill('能量')
  await page.getByPlaceholder('搜索题目...').press('Enter')
  await page.getByRole('dialog').getByText(question.question_text, { exact: true }).click()
  await page.getByRole('button', { name: '推送 (1)', exact: true }).click()
  await expect(student.getByText(question.question_text, { exact: true })).toBeVisible()
  await student.getByRole('radio', { name: /线粒体/ }).check()
  await student.getByRole('button', { name: /提交答案/ }).click()
  await expect.poll(() => score).toBe(10)
  for (const client of [page, student]) {
    const leaderboard = client.locator('.ant-card').filter({ has: client.locator('.ant-card-head-title').filter({ hasText: '排行榜' }) })
    await expect(leaderboard.getByText('学生甲', { exact: true })).toBeVisible()
    await expect(leaderboard.getByText('10分', { exact: true })).toBeVisible()
  }
  await page.getByRole('button', { name: /结\s*束$/ }).click()
  await expect(student.getByPlaceholder('6位房间码')).toBeVisible()
  await student.close()
})
