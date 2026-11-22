// Actual current Electron renderer, native libraries and local deterministic HTTP content.
const { _electron, expect } = require('../../frontend/node_modules/playwright/test')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), http = require('node:http')
const assert = require('node:assert/strict'), crypto = require('node:crypto')
async function main() {
  const root = path.resolve(__dirname, '..'), output = path.resolve(root, '../docs/open-source-integration')
  const executablePath = process.env.KNOWALL_DESKTOP_EXECUTABLE || path.join(root, 'release/oss-round10/win-unpacked/KnowAll Studio.exe')
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'knowall-material-content-'))
  const token = 'material-fixture-token', requests = [], assets = []
  const content = '原生学习 $x^2$，金额 \\$5 和 \\$10。\n\n$$\n\\frac{1}{2}\n$$\n\n```python\ndef learn(value):\n    return value + 1\n```\n\n`$raw$`\n\n|知识|进度|\n|---|---|\n|数学|50%|'
  const feedback = '解释 $y^2$\n\n```js\nconst score = 0.5;\n```'
  const chunks = [{ id: 'content-c1', index: 0, page_range: 'p1', text: '# 第一章 原生学习\n\n'+content }, { id: 'content-c2', index: 1, page_range: 'p2', text: '# 第二章 当前来源\n\n当前第二段原文' }]
  const source = { chunk_ids: ['content-c1'], page_ranges: ['p1'] }, question = { id: 'content-q', question_type: 'short_answer', question_text: content, options: [] }
  const card = { id: 'content-card', front: content, back: feedback }, pack = { id: 'content-pack', stale: false, sections: [{ id: 'content-section', title: '学习章节', notes: content, chunk_ids: source.chunk_ids, flashcards: [card] }] }
  const items = [{ kind: 'question', reason: 'practice', title: '章节笔记', notes: content, question, source }, { kind: 'card', reason: 'due', title: '卡片笔记', notes: '回忆之后评分', card, source }]
  let client, page, report
  const server = http.createServer((req, res) => {
    const uri = new URL(req.url, 'http://localhost').pathname
    res.setHeader('Content-Type', 'application/json')
    if (uri === '/health') return res.end('{"status":"ok"}')
    const user = { id: 'content-user', username: 'content', email: 'content@example.test', is_active: true }
    if (uri === '/api/v1/auth/login') return res.end(JSON.stringify({ access_token: token, user }))
    if (uri === '/api/v1/auth/me') return res.end(JSON.stringify(user))
    if (uri.startsWith('/api/v1/documents/') || uri.startsWith('/api/v1/learning/') || uri === '/api/v1/chat/assistant') {
      if (req.headers.authorization !== `Bearer ${token}`) { res.statusCode = 401; return res.end('{}') }
      let body = ''; req.on('data', bytes => { body += bytes }); req.on('end', () => {
        const value = body ? JSON.parse(body) : undefined; requests.push({ path: uri, authorization: req.headers.authorization, body: value })
        if (uri.endsWith('/chunks')) return res.end(JSON.stringify(chunks))
        if (uri.endsWith('/raw')) { res.setHeader('Content-Type','text/plain; charset=utf-8'); return res.end(chunks[0].text) }
        if (uri === '/api/v1/documents/content') return res.end(JSON.stringify({id:'content',filename:'原生学习.md',file_type:'md',status:'ready',subject:'数学',page_count:2}))
        if (uri.endsWith('/pack')) return res.end(JSON.stringify(pack))
        if (uri.endsWith('/session')) return res.end(JSON.stringify({ items }))
        if (uri.endsWith('/answers')) return res.end(JSON.stringify({is_correct:false,correct_answer:'1;2',score_ratio:0.5,analysis:feedback,source}))
        if (uri.endsWith('/review')) return res.end('{"ok":true}')
        if (uri === '/api/v1/chat/assistant') return res.end(JSON.stringify({message:content}))
        res.end('[]')
      }); return
    }
    res.end('[]')
  })
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve))
  try {
    const env={...process.env,KNOWALL_DESKTOP_DATA_DIR:data};delete env.KNOWALL_SERVER_URL;delete env.ELECTRON_RUN_AS_NODE
    client=await _electron.launch({executablePath,env,timeout:30000});const settings=await client.firstWindow()
    await settings.locator('#server').fill(`http://127.0.0.1:${server.address().port}`)
    const opened=client.waitForEvent('window');await settings.locator('#submit').click();page=await opened
    await page.getByLabel('账号',{exact:true}).fill('content');await page.getByLabel('密码',{exact:true}).fill('test-password-only')
    await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('.product-shell').waitFor()
    page.on('request',r=>assets.push(r.url()));await page.setViewportSize({width:1600,height:1000})
    await page.route('**/assets/MarkdownPreview-*.js',route=>route.abort('failed'))
    await page.goto(new URL('/materials/content',page.url()).href)
    await expect(page.getByText('原文件预览加载失败',{exact:true})).toBeVisible()
    await page.getByRole('button',{name:/第一章 原生学习/}).click()
    async function native(locator) {
      await expect(locator.locator('.katex')).toHaveCount(2);await expect(locator).toContainText('金额 $5 和 $10')
      await expect(locator.locator('p > code')).toHaveText('$raw$');await expect(locator.locator('table td')).toHaveText(['数学','50%'])
      await expect(locator.locator('pre code')).toHaveText('def learn(value):\n    return value + 1\n')
      await expect(locator.locator('pre code span').filter({hasText:/^def$/}).first()).toHaveCSS('color','rgb(166, 38, 164)')
    }
    const article=page.getByRole('article',{name:'资料正文'});await native(article)
    await page.getByRole('tab',{name:'问答',exact:true}).click();await page.getByRole('textbox',{name:'向当前资料提问'}).fill(' 解释公式 ')
    await page.getByRole('button',{name:/发送问题/}).click();await native(page.locator('.material-assistant-reply'))
    assert.deepEqual(requests.find(r=>r.path==='/api/v1/chat/assistant').body,{message:'解释公式',role_preset:'tutor',knowledge_context:chunks[0].text})
    await page.getByRole('tab',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'章节练习',exact:true}).click()
    const q=page.locator('.ant-card').filter({has:page.getByText('随堂练习',{exact:true})});await native(q)
    await page.getByRole('textbox',{name:'你的答案'}).fill(' 1;原答 ');await page.getByRole('button',{name:'提交答案',exact:true}).click()
    await expect(q).toContainText('得分：50%');await expect(q.locator('.ant-alert .katex')).toHaveCount(1)
    await expect(q.locator('.ant-alert pre code')).toHaveText('const score = 0.5;\n')
    const answer=requests.find(r=>r.path.endsWith('/answers')).body;assert.equal(answer.answer,' 1;原答 ');assert.equal(answer.question_id,'content-q')
    await page.getByRole('button',{name:/第二章 当前来源/}).click();await expect(article).toContainText('当前第二段原文')
    await page.getByRole('button',{name:'定位来源原文',exact:true}).click();await native(article)
    await page.getByRole('button',{name:'下一项',exact:true}).click();const review=page.locator('.ant-card').filter({has:page.getByText('复习卡片',{exact:true})})
    await native(review);await expect(review).not.toContainText('解释');await page.getByRole('button',{name:'显示答案',exact:true}).click()
    await expect(review.locator('.katex')).toHaveCount(3);await page.getByRole('button',{name:'良好',exact:true}).click()
    assert.deepEqual(requests.find(r=>r.path.endsWith('/review')).body,{card_id:'content-card',rating:3})
    await expect(page.getByText('本轮练习已完成',{exact:true})).toBeVisible()
    await page.getByRole('tab',{name:'知识点',exact:true}).click();await page.getByText('学习章节',{exact:true}).click()
    await native(page.locator('.material-tools .ant-collapse-content .material-markdown'))
    await page.getByRole('tab',{name:'知识卡',exact:true}).click();const knowledge=page.locator('.material-knowledge-card');await native(knowledge)
    await knowledge.getByRole('button',{name:'显示答案',exact:true}).click();await expect(page.getByTestId('material-card-answer').locator('.katex')).toHaveCount(1)
    await page.screenshot({path:path.join(output,'round-10-packaged-material.png')})
    await page.getByRole('button',{name:/下一段/}).click();await page.reload();await expect(article).toContainText('当前第二段原文')
    await page.evaluate(()=>localStorage.setItem('knowall-theme','dark'));await page.reload();await page.setViewportSize({width:375,height:812})
    await page.getByRole('button',{name:/第一章 原生学习/}).click();await expect(article.locator('pre code span').filter({hasText:/^def$/}).first()).toHaveCSS('color','rgb(86, 156, 214)')
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
    await page.screenshot({path:path.join(output,'round-10-packaged-material-dark.png')})
    const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),resources=path.join(path.dirname(executablePath),'resources')
    const notices=JSON.parse(await fs.readFile(path.join(output,'round-10-runtime-notices.json'),'utf8'));let checked=0
    for(const item of notices)for(const original of item.original_notices){assert.equal(sha(await fs.readFile(path.join(resources,'frontend/third-party',item.name.replace('@','').replace('/','-'),original.file))),original.sha256);checked++}
    const chunk=assets.find(url=>/\/MarkdownContent-[^/]+\.js/.test(url));assert.ok(chunk)
    report={at:new Date().toISOString(),executable:executablePath,app_asar_sha256:sha(await fs.readFile(path.join(resources,'app.asar'))),frontend_index_sha256:sha(await fs.readFile(path.join(resources,'frontend/index.html'))),native_content_chunk_sha256:sha(await fs.readFile(path.join(resources,'frontend/assets',path.basename(new URL(chunk).pathname)))),original_notice_files_checked:checked,requests,assets,passed:true,observations:{failed_original_module_local_boundary:true,material_native_content:true,literal_tutor_context:true,original_answer_partial_score:true,source_location:true,card_reveal_fsrs_rating:true,knowledge_native_content:true,reading_resume:true,dark_narrow:true},simulation:'Actual packaged client/gateway/native libraries with temporary local deterministic account/content HTTP; real account/workers separately smoke-tested'}
  } catch(error) {
    await fs.writeFile(path.join(output,`round-10-packaged-material-diagnostics-${process.env.OSS_EVIDENCE_STAGE||'current'}.json`),JSON.stringify({url:page?.url(),snapshot:page?await page.locator('body').ariaSnapshot():'',requests,assets},null,2));throw error
  } finally {
    if(client)await client.close();await new Promise(resolve=>server.close(resolve));assert.equal(path.dirname(data),os.tmpdir());await fs.rm(data,{recursive:true,force:true})
  }
  report.temporary_data_removed=true;await fs.writeFile(path.join(output,'round-10-packaged-material.json'),JSON.stringify(report,null,2))
  console.log('PASS actual packaged material: native text/REST reply/chapter feedback/knowledge/cards, original payload/source/FSRS, resume/theme/narrow/notices/cleanup')
}
main().catch(error=>{console.error(error);process.exitCode=1})
