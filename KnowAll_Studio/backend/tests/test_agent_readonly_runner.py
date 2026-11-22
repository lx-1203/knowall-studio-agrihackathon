"""Deterministic execution budgets/cancel tests; protocol/SQL HTTP tested separately."""
import asyncio
from types import SimpleNamespace
import pytest
from app.core.api_scheduler.adapters.base import ToolCall,ToolResponse
from app.core.agent_runtime.runner import run_readonly
from app.core.agent_runtime.contracts import ToolResult,AgentExecutionError


class Bridge:
    user_id='alice'
    def __init__(self):self.calls=[]
    def definitions(self):return []
    async def execute(self,call_id,name,args):
        self.calls.append((call_id,name,args))
        return ToolResult(call_id,'success',{'sources':[]})
    async def verified_sources(self,refs):return None


async def test_oversized_parallel_call_batch_fails_before_any_tool_starts():
    bridge=Bridge()
    async def turn(*a,**kw):return ToolResponse('', '{}','simulated',[ToolCall(str(i),'search_documents',{}) for i in range(7)],finish_reason='tool_calls')
    with pytest.raises(AgentExecutionError,match='TOOL_BUDGET_EXCEEDED'):
        await run_readonly(SimpleNamespace(agent_turn=turn),bridge,'find','simulated')
    assert bridge.calls==[]


async def test_timeout_and_cancel_do_not_dispatch_late_tool_results():
    bridge=Bridge();arrived=asyncio.Event()
    async def turn(*a,**kw):
        arrived.set();await asyncio.Event().wait()
    with pytest.raises(AgentExecutionError,match='RUN_TIMEOUT'):
        await run_readonly(SimpleNamespace(agent_turn=turn),bridge,'find','simulated',timeout=.03)
    job=asyncio.create_task(run_readonly(SimpleNamespace(agent_turn=turn),bridge,'find','simulated'))
    await asyncio.wait_for(arrived.wait(),1);job.cancel()
    with pytest.raises(asyncio.CancelledError):await job
    assert bridge.calls==[]


async def test_oversized_history_is_rejected_without_model_call():
    bridge=Bridge();calls=[]
    async def turn(*a,**kw):calls.append(a)
    with pytest.raises(AgentExecutionError,match='CONTEXT_TOO_LARGE'):
        await run_readonly(SimpleNamespace(agent_turn=turn),bridge,'find','simulated',history=[{'role':'user','content':'x'*33000}])
    assert calls==[]


@pytest.mark.parametrize('extra',['S0','S01'])
async def test_valid_source_cannot_hide_an_additional_invalid_numeric_citation(extra):
    bridge=Bridge()
    async def verify(refs):return [{'citation_id':'S1'}] if refs==['S1'] else None
    bridge.verified_sources=verify
    async def turn(*a,**kw):return ToolResponse(f'依据 [S1] 和 [{extra}]', '{}','simulated',[])
    with pytest.raises(AgentExecutionError,match='UNVERIFIED_SOURCES'):
        await run_readonly(SimpleNamespace(agent_turn=turn),bridge,'find','simulated')
