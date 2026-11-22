export const discoveryDocument = { id: 'discovery-doc', filename: '细胞生物学 · 阅读笔记.md', file_type: 'md', status: 'ready', subject: '生物', material_kind: 'knowledge', page_count: 3 }
export const discoveryChunks = [{ id: 'discovery-c1', index: 0, text: '# 细胞的结构\n\n细胞膜保护内部环境。' }, { id: 'discovery-c2', index: 1, text: '# 细胞核与遗传\n\n细胞核保存遗传信息。DNA通过复制与转录传递信息。' }]
export const discoveryHit = { id: 'discovery-c2', text: discoveryChunks[1].text, metadata: { doc_id: discoveryDocument.id, filename: discoveryDocument.filename, chunk_index: 1, page_range: '2–3' } }
export const discoveryCard = { id: 'discovery-card', front: '细胞核有什么作用？', back: '保存遗传信息。'+'完整答案需要滚动查看。'.repeat(45), card_type: 'basic', tags: {} }
export const discoveryTrees = [{ tree_id: 'tree-a', name: '细胞结构与生命活动', created_at: '2026-10-13' }, { tree_id: 'tree-b', name: '遗传信息的传递', created_at: '2026-10-13' }]
export const discoveryTreeDetails = {
 'tree-a': { tree_id: 'tree-a', tree_data: { tree: { nodes: [{ id: 'a', label: '细胞结构', summary: '从整体到各个组成部分。', children: [{ id: 'a1', label: '细胞膜', summary: '控制物质交换。', children: [] }, { id: 'a2', label: '细胞核', summary: '储存遗传信息。'.repeat(30), children: [] }] }] } } },
 'tree-b': { tree_id: 'tree-b', tree_data: { tree: { nodes: [{ id: 'b', label: 'DNA复制与转录的完整过程及其在不同细胞中的表现', summary: '这是另一棵树的内容。', children: [] }] } } }
}
export async function mockDiscovery(page) {
 const state = { faults: new Set(), delay: '', delayMs: 1600, calls: [], sync: new Map(), paging: false, searchPaging: false, hits: null }
 await page.addInitScript(() => { localStorage.setItem('knowall_token', 'isolated-discovery'); localStorage.setItem('knowall_user', JSON.stringify({id:'discovery-user', username:'小林'})) })
 await page.route('**/api/**', async route => {
  const url = new URL(route.request().url()), path = url.pathname, method = route.request().method(), input = route.request().postDataJSON()
  state.calls.push({path,method,input}); if (state.delay===path) await new Promise(r=>setTimeout(r,state.delayMs))
  if (state.faults.has(path)) return route.fulfill({status:503,json:{detail:'隔离测试：暂时无法加载'}})
  let value
  if (path==='/api/v1/search/' || path==='/api/v1/search/rag') {
   const results = input?.query==='不存在' ? [] : state.hits || (state.searchPaging ? Array.from({length:input.top_k},(_,i)=>({...discoveryHit,id:'hit-'+i})) : [discoveryHit])
   value = path.endsWith('/rag') ? {sources:results, context:results.length?discoveryHit.text:'', has_results:!!results.length} : {results}
  } else if (path==='/api/v1/flashcards/search') value={results:url.searchParams.get('q')==='不存在'?[]:[discoveryCard]}
  else if (path==='/api/v1/documents/') value=[discoveryDocument]
  else if (path==='/api/v1/documents/discovery-doc') value=discoveryDocument
  else if (path==='/api/v1/documents/discovery-doc/chunks') value=discoveryChunks
  else if (path==='/api/v1/knowledge/trees') value=state.paging ? (Number(url.searchParams.get('offset'))===0 ? Array.from({length:50},(_,i)=>({tree_id:'page-'+i,name:'知识树 '+i,created_at:'2026-10-13'})) : [discoveryTrees[1]]) : discoveryTrees
  else if (path.startsWith('/api/v1/knowledge/tree/')) value=discoveryTreeDetails[path.split('/').at(-1)]
  else if (/\/state\/(note|text_progress)$/.test(path)) {
   const kind=path.split('/').at(-1);value=state.sync.get(path)||{version:1,account_id:'discovery-user',document_id:'discovery-doc',kind,revision:0,payload:null,source_version:null,current_source_version:'source-discovery',stale:false}
   if(method==='PUT'){value={...value,revision:value.revision+1,payload:input.payload,source_version:input.source_version};state.sync.set(path,value)}
  }
  else { return route.fulfill({status:404,json:{detail:'Unexpected isolated endpoint '+path}}) }
  return route.fulfill({json:value})
 }); return state
}
