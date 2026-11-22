"""Disposable UI walkthrough using production document/auth/learning routes.

Run from backend: python scripts/material_ui_preview.py
Uses its own temporary SQLite and example files; never opens the user's DB.
AI generation is disabled for this walkthrough. Ctrl+C removes temporary data.
"""
import os
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
preview_data = tempfile.TemporaryDirectory(prefix='knowall-material-preview-')
root = Path(preview_data.name)
os.environ.update(KNOWALL_IGNORE_ENV_FILE='true', KNOWALL_DATA_ROOT=str(root),
                  DATABASE_URL='sqlite+aiosqlite:///' + (root / 'preview.sqlite').as_posix(),
                  JWT_SECRET=secrets.token_urlsafe(48), LOCAL_AUTO_LOGIN='true', DEBUG='false')

from contextlib import asynccontextmanager
import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api import auth, documents, learning
from app.database import Base, engine

EXAMPLES = [
    ('生物 · 细胞与遗传.md', '生物', 'knowledge', '''# 细胞与遗传

## 第一章 细胞：生命活动的基本单位

从一片叶子到我们自己的身体，生命的活动都发生在细胞之中。学习本章时，先理解细胞各部分的分工，再思考它们如何配合。

### 细胞的结构

| 结构 | 作用 | 帮助理解的例子 |
| --- | --- | --- |
| 细胞膜 | 选择性地控制物质进出 | 细胞的边界与门卫 |
| 细胞核 | 储存遗传信息 | 细胞的信息中心 |
| 线粒体 | 为细胞活动提供能量 | 能量转换的场所 |

> 理解提示：这些比喻只是帮助记忆，细胞结构的真实功能应以教材描述为准。

### 观察与思考

1. 为什么细胞膜具有选择透过性？
2. 成熟的哺乳动物红细胞没有细胞核，这能说明什么？
3. 如果线粒体功能受损，哪些细胞更容易受到影响？

## 第二章 遗传信息与 DNA

DNA 保存遗传信息。基因是具有遗传效应的 DNA 片段。理解“DNA、基因与染色体”的关系，比只记住三个名称更重要。

### 信息的表达

遗传信息通常通过转录形成 RNA，再通过翻译形成蛋白质。蛋白质参与结构组成、催化反应与调控等多种生命活动。

### 本章自查

- 我能用自己的话解释基因与 DNA 的关系吗？
- 我能说清转录和翻译发生了什么吗？
- 我还有哪些问题想查原文或向老师提问？
'''),
    ('历史 · 工业革命学习笔记.md', '历史', 'knowledge', '''# 工业革命学习笔记

## 背景与条件

研究工业革命时，可以从技术、资金、劳动力、市场与制度等方面寻找原因。把多个条件放在一起理解，有助于避免单一原因解释。

## 技术与生产方式的变化

机械化生产逐步发展，工厂成为重要的生产组织形式。蒸汽动力推动运输与工业生产的变化。

## 社会影响

城市化、阶级关系与生活方式发生变化，生产力增长也伴随着劳动条件等问题。

> 阅读任务：在原教材中找到分别支持积极影响和社会问题的史料，并记录出处。
'''),
    ('生物 · 章节练习卷.md', '生物', 'question_bank', '''# 生物章节练习卷

## 一、选择题

1. 细胞中储存遗传信息的主要场所是？

- A. 细胞膜
- B. 细胞核
- C. 细胞壁
- D. 液泡

## 二、简答题

2. 用自己的话解释细胞膜的选择透过性。

3. 说明 DNA、基因与染色体的关系。

## 参考答案

1. B。细胞核是储存遗传信息的主要场所。
2. 细胞膜允许某些物质进入或离开细胞，同时限制其他物质通过。
3. 基因是具有遗传效应的 DNA 片段，DNA 与蛋白质等构成染色体。
'''),
]

@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
        session = await client.post('/api/v1/auth/local-session', json={}, headers={'X-KnowAll-Local': '1'})
        session.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + session.json()['access_token']
        for filename, subject, kind, text in EXAMPLES:
            result = await client.post('/api/v1/documents/upload', files={'file': (filename, text.encode(), 'text/markdown')})
            result.raise_for_status()
            doc_id = result.json()['document_id']
            classified = await client.patch(f'/api/v1/documents/{doc_id}/organization', json={'subject': subject, 'material_kind': kind})
            classified.raise_for_status()
    print('PREVIEW_READY disposable example data; no paid AI calls', flush=True)
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(learning.router)

@app.middleware('http')
async def prevent_paid_generation(request, call_next):
    if request.url.path.endswith('/generate') or request.url.path.startswith('/api/v1/chat'):
        return JSONResponse(status_code=503, content={'detail': '示例预览未启用 AI 模型，请在自己的系统中配置模型后使用。'})
    return await call_next(request)

if __name__ == '__main__':
    try:
        uvicorn.run(app, host='127.0.0.1', port=8001, log_level='warning')
    finally:
        preview_data.cleanup()
