"""Read-only next-candidate proof; RAG retrieval bytes explicitly simulated."""
import importlib
from pathlib import Path
import os, json, hashlib
from datetime import datetime
from app.api import chat
from app.core.assistant import Assistant
from app.core.rag_assistant import RAGAssistant
from tests.test_round55_context_completion import contextual, tenancy, server

async def test_native_rag_current_question_is_sent_twice(contextual, monkeypatch):
    t=contextual
    rag=importlib.import_module('app.core.rag_assistant')
    monkeypatch.setattr(rag,'rag_query',lambda query,top_k:'明确模拟的本地参考资料')
    monkeypatch.setattr(chat.ai_assistant,'chat',Assistant.chat.__get__(chat.ai_assistant,Assistant))
    monkeypatch.setattr(chat.rag_assistant,'chat_with_rag',RAGAssistant.chat_with_rag.__get__(chat.rag_assistant,RAGAssistant))
    monkeypatch.setattr(chat.settings,'rag_provider','chromadb')
    response=await t.client.post('/api/v1/chat/assistant/rag',headers=t.headers('alice'),json={'message':'RAG-unique-question55','model':'openai/chosen-model'})
    assert response.status_code==200,response.text
    calls=t.provider_calls
    users=[m['content'] for m in calls[0]['messages'] if m['role']=='user']
    assert len(calls)==1 and len(users)==2 and users[0]=='RAG-unique-question55' and 'RAG-unique-question55' in users[1]
    out=Path(os.environ['KNOWALL_READING_NATIVE_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1]
    record=dict(at=datetime.now().astimezone().isoformat(),status=response.status_code,provider_calls=calls,current_question_occurrences=2,
      production_modified=False,paid_calls=0,scope='native JWT/HTTP/temporary SQL/RAGAssistant/Assistant/OpenAI adapter; only retrieval text simulated; proves duplicate prompt, not quota lock',
      source_sha256={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in ['app/api/chat.py','app/core/rag_assistant.py','app/core/assistant/__init__.py']})
    (out/'rag-history-candidate.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
