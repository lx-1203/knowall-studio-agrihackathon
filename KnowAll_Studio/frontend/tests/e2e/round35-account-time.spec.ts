import { test, expect } from './fixtures'

const zones = ['Asia/Shanghai', 'UTC', 'America/New_York']
for (const zone of zones) test.describe(zone, () => {
  test.use({ timezoneId: zone })
  test('profile, binding, history and notification preserve legacy and offset instants', async ({ page }) => {
    const cases = [
      ['old UTC', '2026-10-09T19:40:00.123456'],
      ['UTC', '2026-10-09T19:40:00Z'],
      ['positive', '2026-10-10T03:40:00+08:00'],
      ['negative', '2026-10-09T15:40:00-04:00'],
      ['year boundary', '2026-12-31T23:59:59Z'],
      ['DST boundary', '2026-03-08T07:00:00Z'],
    ]
    const localTime = (raw: string) => {
      const value = new Date(raw.includes('+') || /Z$|-[0-9]{2}:[0-9]{2}$/.test(raw) ? raw : raw + 'Z')
      const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(value)
      const part = (key: string) => parts.find(p => p.type === key)!.value
      return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
    }
    const profile = { id: 'test', username: '学习者', email: 'time@example.com', nickname: '', created_at: cases[0][1] }
    await page.route('**/api/v1/user/profile', r => r.fulfill({ json: profile }))
    await page.route('**/api/v1/user/binds', r => r.fulfill({ json: [{ id: 'bound', provider: 'github', provider_name: 'GitHub', is_bound: true, bound_at: cases[0][1] }] }))
    await page.route('**/api/v1/user/history**', r => r.fulfill({ json: { total: cases.length, items: cases.map(([name, created_at], i) => ({ id: String(i), action_type: 'test', action_label: name, created_at })) } }))
    await page.route('**/api/v1/notifications**', r => r.fulfill({ json: { total: cases.length, unread_count: cases.length, items: cases.map(([title, created_at], i) => ({ id: String(i), title, content: 'test', category: 'system', is_read: false, created_at })) } }))
    await page.goto('/personal', { waitUntil: 'domcontentloaded' })
    expect(await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)).toBe(zone)
    await expect(page.getByText(/^注册时间:/)).toHaveText(`注册时间: ${localTime(cases[0][1])}`)
    await page.getByRole('tab', { name: '账号安全' }).click()
    await expect(page.getByText(`已绑定 · ${localTime(cases[0][1]).slice(0, 10)}`, { exact: true })).toBeVisible()
    for (const tab of ['操作记录', '消息通知']) {
      await page.getByRole('tab', { name: tab }).click()
      for (const [name, raw] of cases) await expect(page.locator('.ant-tabs-tabpane-active .ant-list-item').filter({ has: page.getByText(name, { exact: true }) }).getByText(localTime(raw), { exact: true })).toBeVisible()
    }
  })
  test('missing profile and invalid history times render a neutral placeholder', async ({ page }) => {
    await page.route('**/api/v1/user/profile', r => r.fulfill({ json: { id: 'test', username: '学习者', email: 'test@example.com', created_at: null } }))
    await page.route('**/api/v1/user/history**', r => r.fulfill({ json: { total: 2, items: [{ id: 'bad', action_type: 'test', action_label: '坏时间', created_at: 'nonsense' }, { id: 'missing', action_type: 'test', action_label: '缺时间', created_at: '' }] } }))
    await page.goto('/personal', { waitUntil: 'domcontentloaded' })
    await expect(page.getByText(/^注册时间:/)).toHaveText('注册时间: —')
    await page.getByRole('tab', { name: '操作记录' }).click()
    for (const name of ['坏时间', '缺时间']) await expect(page.locator('.ant-list-item').filter({ hasText: name }).getByText('—', { exact: true })).toBeVisible()
  })
})
