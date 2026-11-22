import { test, expect } from './fixtures'
const zones = [['Asia/Shanghai', '2026-10-10', '2027-01-01'], ['UTC', '2026-10-09', '2026-12-31'], ['America/New_York', '2026-10-09', '2026-12-31']]
for (const [zone, night, year] of zones) test.describe(zone, () => {
  test.use({ timezoneId: zone })
  test('sidebar normalizes legacy and offset dates and still selects stored messages', async ({ page }) => {
    const cases = [['旧UTC', '2026-10-09T19:40:00.123456', night], ['正偏移', '2026-10-10T03:40:00+08:00', night], ['负偏移', '2026-10-09T15:40:00-04:00', night], ['跨年', '2026-12-31T23:59:59Z', year], ['坏日期', 'nonsense', '—']]
    await page.route('**/api/v1/chat/conversations', route => route.fulfill({ json: cases.map(([title, created_at], i) => ({ id: String(i), title, created_at, role_preset: 'tutor' })) }))
    await page.route('**/api/v1/chat/roles', route => route.fulfill({ json: { tutor: { name: '学习辅导' } } }))
    await page.route('**/api/v1/chat/conversations/0', route => route.fulfill({ json: { conversation_id: '0', title: '旧UTC', role_preset: 'tutor', messages: [{ id: 'message', role: 'user', content: '原有对话保持', created_at: cases[0][1] }] } }))
    await page.goto('/chat', { waitUntil: 'domcontentloaded' })
    for (const [title, , date] of cases) await expect(page.getByText(title, { exact: true }).locator('..').getByText(date, { exact: true })).toBeVisible()
    await page.getByText('旧UTC', { exact: true }).click()
    await expect(page.getByText('原有对话保持', { exact: true })).toBeVisible()
  })
})
