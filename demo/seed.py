"""Populate only the isolated video database with prepared teaching examples."""
import os
import sys
import hashlib
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'KnowAll_Studio/backend/data'
os.environ.update(KNOWALL_IGNORE_ENV_FILE='true', KNOWALL_DATA_ROOT=str(DATA),
                  DATABASE_URL='sqlite+aiosqlite:///' + (DATA / 'demo.db').as_posix(),
                  GRAPHRAG_ENABLED='false', DEBUG='false')
sys.path.insert(0, str(ROOT / 'KnowAll_Studio/backend'))
from app.models import User, Document, DocumentChunk, Deck, Flashcard, ReviewSchedule, QuestionBank
from app.models.study_pack import StudyPack
from app.database import async_session, engine
from app.core.learning_workbench import source_fingerprint, question_quality

CHAPTERS = [
('遗传信息的传递', '''# 遗传信息的传递

## 先抓住一条主线
细胞以 DNA 保存遗传信息，经过转录形成 RNA，再通过翻译合成蛋白质。

**DNA → RNA → 蛋白质**

| 过程 | 模板 | 主要产物 |
| --- | --- | --- |
| 复制 | DNA | DNA |
| 转录 | DNA | RNA |
| 翻译 | mRNA | 多肽链 |

## 学习目标
- 分清复制、转录与翻译。
- 用自己的话描述遗传信息的流向。
- 把关键概念连接起来，再用练习检验理解。

> 复习提示：不要只记箭头，试着说清每一步的模板和产物。
'''),
('DNA 复制：把信息准确传下去', '''# DNA 复制：把信息准确传下去

## 半保留复制
每个子代 DNA 双链都保留一条亲代链，并合成一条新链。

## 三个关键词
1. **模板**：亲代 DNA 的两条链分别作为模板。
2. **互补**：依据碱基互补配对原则加入核苷酸。
3. **方向**：新链沿 5′ → 3′ 方向延伸。

## 易混点对比
| 概念 | 核心区别 |
| --- | --- |
| 前导链 | 沿复制叉前进方向连续合成 |
| 后随链 | 以冈崎片段的形式不连续合成 |

**自我检查**：为什么复制需要引物？因为 DNA 聚合酶需要已有的游离 3′-OH 作为延伸起点。
'''),
('转录与翻译：从信息到功能', '''# 转录与翻译：从信息到功能

## 转录：生成 RNA
RNA 聚合酶以 DNA 的一条链为模板，按互补配对原则合成 RNA。

## 翻译：合成多肽链
核糖体读取 mRNA 上的密码子；tRNA 携带相应氨基酸参与多肽链的合成。

| 参与者 | 作用 |
| --- | --- |
| mRNA | 提供密码子序列 |
| tRNA | 携带氨基酸并识别密码子 |
| 核糖体 | 完成多肽链合成的主要场所 |

## 用比较代替死记
转录的产物是 RNA；翻译的产物是多肽链。两者的模板、参与分子和产物不同。
'''),
('章节回顾与复习清单', '''# 章节回顾与复习清单

## 一张表回顾整章
| 学习问题 | 回忆线索 |
| --- | --- |
| 信息保存在哪里？ | DNA |
| 如何复制给子代细胞？ | 半保留复制 |
| 如何表达信息？ | 转录与翻译 |

## 今日复习
- [ ] 合上资料，复述中心法则。
- [ ] 画出复制、转录与翻译的关系。
- [ ] 完成章节练习，检查易混概念。
- [ ] 用知识卡复习，按掌握程度安排下一次回顾。

> 看懂之后，试着不看答案讲出来。
''')]
QUESTIONS = [
('遗传信息从 DNA 流向 RNA 的过程称为什么？', ['复制', '转录', '翻译', '逆转录'], 'B', '转录以 DNA 为模板合成 RNA；翻译以 mRNA 为模板合成多肽链。'),
('DNA 半保留复制的含义是什么？', ['子代只含亲代链', '子代只含新合成链', '每个子代双链含一条亲代链和一条新链', 'DNA 不能复制'], 'C', '每个子代 DNA 双链中，一条链来自亲代，另一条链为新合成链。'),
('翻译过程中，携带氨基酸的分子是哪一个？', ['DNA', 'mRNA', 'tRNA', 'RNA 聚合酶'], 'C', 'tRNA 携带氨基酸，并通过反密码子识别 mRNA 上对应的密码子。'),
('回顾复制、转录和翻译，哪一项配对正确？', ['复制—RNA', '转录—多肽链', '翻译—多肽链', '翻译—DNA'], 'C', '复制生成 DNA，转录生成 RNA，翻译生成多肽链。')]
CARDS = [
('中心法则的经典信息流是什么？', 'DNA → RNA → 蛋白质。先转录，再翻译。'),
('什么是 DNA 半保留复制？', '每个子代 DNA 双链由一条亲代链和一条新合成链组成。'),
('转录与翻译的主要区别是什么？', '转录以 DNA 为模板合成 RNA；翻译以 mRNA 为模板合成多肽链。'),
('学习这一章，应该怎样自我检查？', '合上资料复述概念，再通过习题和知识卡检查掌握情况。')]

async def main():
    async with async_session() as db:
        if await db.get(Document, 'demo-biology'):
            print('Prepared demo dataset already exists.'); return
        if not await db.get(User, 'local_user'):
            db.add(User(id='local_user', username='演示学习空间', email='demo@knowall.invalid', password_hash='demo-no-password-login', is_active=True))
        docdir = DATA / 'documents'; docdir.mkdir(exist_ok=True)
        whole = '\n\n'.join(text for _, text in CHAPTERS)
        path = docdir / 'molecular-biology.md'; path.write_text(whole, encoding='utf-8')
        db.add(Document(id='demo-biology', filename='分子生物学 · 遗传信息的传递', file_type='md', status='ready',
                        local_path=str(path), sha256=hashlib.sha256(whole.encode()).hexdigest(), file_size=len(whole.encode()),
                        page_count=4, metadata_={'knowledge_owner_id':'local_user','subject':'分子生物学','material_kind':'mixed','video_demo':True}))
        await db.flush()
        chunks = []
        for i, (title, text) in enumerate(CHAPTERS):
            chunk = dict(id=f'demo-chapter-{i+1}', chunk_index=i, text_content=text, page_range=f'第 {i+1} 章')
            chunks.append(chunk); db.add(DocumentChunk(doc_id='demo-biology', **chunk))
        for j, (name, subject, desc) in enumerate([
            ('高等数学 · 极限与连续', '高等数学', '# 极限与连续\n\n通过极限刻画函数的局部变化，使用定义与例题建立理解。'),
            ('大学英语 · 学术阅读笔记', '大学英语', '# 学术阅读笔记\n\n先定位主题句，再整理文章结构和论证关系。'),
            ('Python 程序设计 · 数据结构', '程序设计', '# 数据结构\n\n比较列表、元组与字典的结构和使用场景。'),
        ]):
            p=docdir/f'material-{j}.md'; p.write_text(desc, encoding='utf-8')
            db.add(Document(id=f'demo-material-{j}', filename=name,file_type='md',status='ready',local_path=str(p),sha256=hashlib.sha256(desc.encode()).hexdigest(),metadata_={'knowledge_owner_id':'local_user','subject':subject,'material_kind':'knowledge'}))
            db.add(DocumentChunk(id=f'demo-material-chunk-{j}',doc_id=f'demo-material-{j}',chunk_index=0,text_content=desc))
        db.add(Deck(id='demo-deck', user_id='local_user', name='分子生物学 · 核心概念', description='演示用预先准备的课程知识卡', card_count=len(CARDS)))
        await db.flush()
        sections=[]
        for i, ((title,text),q,c) in enumerate(zip(CHAPTERS,QUESTIONS,CARDS)):
            qt,opts,answer,analysis=q
            payload=dict(id=f'demo-question-{i}',question_type='single_choice',question_text=qt,
                         options=[{'label':chr(65+k),'text':v} for k,v in enumerate(opts)],answer=answer,analysis=analysis)
            assert question_quality(payload)['eligible'], question_quality(payload)
            db.add(QuestionBank(id=payload['id'],question_type='single_choice',question_text=qt, options=payload['options'],correct_answer=answer,analysis=analysis,source_chunk_id=chunks[i]['id'],tags=['分子生物学']))
            card=dict(id=f'demo-card-{i}',front=c[0],back=c[1],hints='回忆这一步的模板与产物')
            db.add(Flashcard(**card,card_type='qa',deck_id='demo-deck',tags=['分子生物学']))
            db.add(ReviewSchedule(id=f'demo-schedule-{i}',card_id=card['id'],state='new'))
            sections.append(dict(id=f'section-{i}',title=title,chunk_ids=[chunks[i]['id']],page_ranges=[chunks[i]['page_range']],notes='### '+title+'\n\n'+c[1],knowledge_point_id=f'demo-kp-{i}',questions=[payload],flashcards=[card],errors=[]))
        db.add(StudyPack(id='demo-pack',user_id='local_user',document_id='demo-biology',title='遗传信息的传递 · 章节学习包',source_hash=source_fingerprint(chunks),sections=sections,deck_id='demo-deck',config={'question_count':4,'card_count':4,'question_types':['single_choice'],'origin':'prepared-video-demo'}))
        await db.commit()
    await engine.dispose()
    print('Prepared demo data: 4 materials, 4 chapters, 4 questions, 4 cards. No generated-model output is claimed.')

if __name__=='__main__': asyncio.run(main())
