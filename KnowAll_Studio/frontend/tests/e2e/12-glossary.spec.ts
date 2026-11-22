import { test, expect, type Page } from '@playwright/test'

// 名词解释库 e2e：依赖后端 + 词库数据（GLOSSARY_E2E=1 时运行，否则 skip 不误报失败）
const hasBackend = !!process.env.GLOSSARY_E2E
test.skip(!hasBackend, '需要后端 + 词库数据（设置 GLOSSARY_E2E=1 运行）')

/** 通过后端登录注入 token（React 应用要求登录态才能访问内部页面）。注册/登录一个随机测试用户。 */
async function loginAndSeed(page: Page): Promise<void> {
  const username = `glossary_e2e_${Date.now()}`
  const password = 'test1234'
  // 先访问应用页面（建立同源），再在页面上下文执行注册/登录并写入 token，最后 reload
  await page.goto('/')
  await page.evaluate(async ({ username, password }) => {
    const randEmail = username + '@test.com'
    await fetch('/api/v1/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, email: randEmail }),
    }).catch(() => {})
    const resp = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    const data = await resp.json()
    localStorage.setItem('knowall_token', data.access_token)
  }, { username, password })
  // 重新加载，让应用在启动时读到已写入的 token
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
}

test.describe('名词解释库', () => {
  test('检索页可搜索并跳详情', async ({ page }) => {
    await loginAndSeed(page)
    await page.goto('/glossary')
    await expect(page.getByRole('heading', { name: /名词解释库/ })).toBeVisible()

    // 统计信息可见（词库有 40 条）
    await expect(page.getByText(/共\s*\d+\s*个(?:术语|词条)/)).toBeVisible()

    // 搜索一个已知术语（词库有数据时）
    await page.getByPlaceholder(/搜索术语/).fill('基因')
    await page.keyboard.press('Enter')
    const row = page.locator('table tbody tr').first()
    await expect(row).toBeVisible()
  })

  test('详情页可访问并显示来源', async ({ page }) => {
    await loginAndSeed(page)
    // 基因组（词库中存在，规范名 "3.1.2 基因组"，source_type=ai / 知识总纲来源）
    const term = encodeURIComponent('3.1.2 基因组')
    await page.goto(`/glossary/${term}`)
    await expect(page.locator('.ant-card')).toBeVisible({ timeout: 15000 })
    // 术语名可见
    await expect(page.getByRole('heading', { name: /基因组/ })).toBeVisible()
    // 来源标识可见（AI 生成 或 资料库）
    await expect(page.getByText(/AI 生成|资料库|权威来源|网络出处/).first()).toBeVisible()
  })

  test('刷题页术语高亮并弹卡', async ({ page }) => {
    await page.goto('/molbio-quiz.html')
    // 等待词库 JSON + 页面 render
    await page.waitForTimeout(2500)

    const term = page.locator('.glossary-term').first()
    if (await term.count()) {
      await term.click()
      // 浮窗出现（含术语名 + 查看详情）
      await expect(page.locator('.glossary-popup')).toBeVisible()
      await expect(page.locator('.glossary-popup .gt-title')).toBeVisible()
      await expect(page.locator('.glossary-popup .gt-foot a')).toContainText('查看详情')
    } else {
      // 词库为空时页面无高亮，但不应报错——验证题干仍正常渲染
      await expect(page.locator('.qtext')).toBeVisible()
    }
  })

  test('刷题页弹卡跳详情（浮窗内链接）', async ({ page }) => {
    await page.goto('/molbio-quiz.html')
    await page.waitForTimeout(2500)

    const term = page.locator('.glossary-term').first()
    if (await term.count()) {
      await term.click()
      const detailLink = page.locator('.glossary-popup .gt-foot a')
      const href = await detailLink.getAttribute('href')
      expect(href).toContain('/glossary/')
      // 点击后（新窗口/同窗）应导航到详情页
      await Promise.all([
        page.waitForURL(/\/glossary\//),
        detailLink.click(),
      ])
    }
  })
})
