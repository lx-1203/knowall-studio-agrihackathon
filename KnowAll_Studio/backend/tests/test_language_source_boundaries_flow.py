"""Guard the direct Agent entry used by orchestration before any model consumption."""
import json
import pytest
from app.models import Document,KnowledgeSummary,LanguageVocabulary,APICache
from app.core.agents.language_agent import LanguageAgent
from tests.test_language_cache_recovery_flow import language_flow,cache_flow,flow,rows,VALID

@pytest.mark.parametrize('mode',['missing-document','wrong-source','combined-secondary'])
async def test_language_agent_source_guard_before_paid_call(language_flow,mode):
    f=language_flow
    async with f.factory() as db:
        db.add(Document(id='other',filename='other.txt',file_type='txt',sha256='source-boundary-test'));await db.flush()
        if mode=='combined-secondary':
            db.add(KnowledgeSummary(id='combined',document_id='other',document_ids=['other','source'],content_md='# 合并词汇\n能量'))
        await db.commit()
    f.adapters['primary'].content=json.dumps(VALID)
    r=await LanguageAgent().run('combined' if mode=='combined-secondary' else 'summary',
        'missing' if mode=='missing-document' else 'other' if mode=='wrong-source' else 'source')
    f.evidence['Result']={'Status':r.status,'Error':r.error}
    if mode=='combined-secondary':assert r.status=='success' and len(f.adapters['primary'].calls)==1
    else:
        assert r.status=='error'
        assert f.adapters['primary'].calls==[]
        assert await rows(f,LanguageVocabulary)==[] and await rows(f,APICache)==[]
