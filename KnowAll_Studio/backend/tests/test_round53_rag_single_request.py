"""Existing native retrieval fallback must not retry failed model calls."""
from types import SimpleNamespace
import pytest
from app.core import rag_assistant as module
from app.core.rag_assistant import RAGAssistant

@pytest.mark.parametrize('failure',['generation','context-format','retrieval','empty-context'])
async def test_native_pipeline_retrieval_fallback_has_one_model_request(monkeypatch,failure):
    calls=[]
    class Pipeline:
        async def search(self,*args,**kw):
            if failure=='retrieval': raise RuntimeError('retrieval unavailable')
            class Result:
                @property
                def context(self):
                    if failure=='context-format': raise RuntimeError('format unavailable')
                    return '' if failure=='empty-context' else 'native context'
            return Result()
    monkeypatch.setattr(module,'get_pipeline_by_name',lambda name:Pipeline())
    async def generate(*args,**kw):
        calls.append((args,kw))
        if failure=='generation': raise RuntimeError('provider failed after request')
        return 'complete'
    monkeypatch.setattr(module.base_assistant,'chat',generate)
    if failure=='generation':
        with pytest.raises(RuntimeError,match='provider'):await RAGAssistant()._chat_with_pipeline('q','tutor',[],'model',3,'alice','custom')
    else:
        assert await RAGAssistant()._chat_with_pipeline('q','tutor',[],'model',3,'alice','custom')=='complete'
    assert len(calls)==1
    assert calls[0][1]['user_id']=='alice'
