import { test, expect } from './fixtures'
for (const zone of ['Asia/Shanghai', 'UTC', 'America/New_York']) test.describe(zone, () => {
  test.use({ timezoneId: zone })
  test('log table uses local instants, paginates, refreshes and explains cloud storage', async ({ page }) => {
    const cases = [
      ['legacy', '2026-10-09T19:40:00.123456'], ['zulu', '2026-10-09T19:40:00Z'],
      ['positive', '2026-10-10T03:40:00+08:00'], ['negative', '2026-10-09T15:40:00-04:00'],
      ['year', '2026-12-31T23:59:59Z'], ['dst', '2026-03-08T07:00:00Z'],
      ['invalid', 'nonsense'], ['empty', ''],
    ]
    let requests = 0
    await page.route('**/api/v1/admin/logs?*', route => {
      requests++; expect(new URL(route.request().url()).searchParams.get('limit')).toBe('20')
      return route.fulfill({ json: cases.map(([model_name, created_at], i) => ({ id: String(i), model_name, created_at, task_type: 'chat', duration_ms: 42, tokens_input: 0, tokens_output: 3, success: i%2===0, from_cache: i%3===0 })) })
    })
    await page.goto('/settings', { waitUntil: 'domcontentloaded' })
    expect(await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)).toBe(zone)
    const card = page.locator('.ant-card').filter({ has: page.getByText('API 调用日志', { exact: true }) })
    const local = (raw: string) => {
      if (raw === '' || raw === 'nonsense') return '—'
      const date = new Date(/Z$|[+-][0-9]{2}:[0-9]{2}$/.test(raw) ? raw : raw + 'Z')
      const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(date)
      const p = (key: string) => parts.find(x => x.type === key)!.value
      return `${p('year')}-${p('month')}-${p('day')} ${p('hour')}:${p('minute')}:${p('second')}`
    }
    for (const [model, raw] of cases.slice(0, 5)) await expect(card.getByRole('row').filter({ has: page.getByText(model, { exact: true }) }).getByRole('cell').nth(7)).toHaveText(local(raw))
    await card.locator('.ant-pagination-item-2').click()
    for (const [model, raw] of cases.slice(5)) await expect(card.getByRole('row').filter({ has: page.getByText(model, { exact: true }) }).getByRole('cell').nth(7)).toHaveText(local(raw))
    await card.getByRole('button', { name: /刷\s*新/ }).click()
    await expect.poll(() => requests).toBe(2)
    await expect(page.getByText('云端资料和学习操作需要连接服务；断网后请恢复连接再继续。', { exact: true })).toBeVisible()
    await expect(page.getByText(/所有原始文档 100% 保存在本地|本机 SQLite 数据库|断网后：复习/)).toHaveCount(0)
  })
})
