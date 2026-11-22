"""Real research/book pipelines, disposable SQL; all AI/tools explicitly simulated."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from tests.test_generation_state_flow import flow

@pytest.fixture
async def paused(flow):
    entered=asyncio.Event();cleaned=asyncio.Event();calls=[]
    async def simulated(prompt,**kwargs):
        calls.append(prompt);entered.set()
        try:await asyncio.Event().wait()
        finally:cleaned.set()
    flow.model.side_effect=simulated
    return SimpleNamespace(**vars(flow),entered=entered,cleaned=cleaned,paused_calls=calls)

async def start(flow,kind):
    return await flow.agent.start_research('明确模拟研究') if kind=='research' else await flow.compiler.create_book('明确模拟教材',source_doc_ids=['source'])

def background_jobs(before):
    return [j for j in asyncio.all_tasks()-before if j is not asyncio.current_task() and not j.done()]

@pytest.mark.parametrize('kind',['research','book'])
@pytest.mark.parametrize('prestart',[False,True])
async def test_actual_cancellation_reaches_error_even_before_first_stage(paused,kind,prestart):
    before=asyncio.all_tasks();item=await start(paused,kind);jobs=background_jobs(before)
    assert len(jobs)==1
    if not prestart:await asyncio.wait_for(paused.entered.wait(),2)
    jobs[0].cancel();result=await asyncio.gather(*jobs,return_exceptions=True);await asyncio.sleep(0)
    assert isinstance(result[0],asyncio.CancelledError)
    assert item.status.value=='error' and item.error and item.progress<1, 'cancelled generation left a permanent running state'
    if not prestart:assert paused.cleaned.is_set() and len(paused.paused_calls)==1
    if kind=='research':
        assert item.completed_at>0
        events=[v async for v in paused.agent.stream_progress(item.task_id)]
        assert events[-1]['event']=='error' and all(v['event']!='complete' for v in events)

async def test_book_delete_cancels_and_close_can_still_await_cleanup(paused):
    before=asyncio.all_tasks();book=await start(paused,'book');job=background_jobs(before)[0]
    await asyncio.wait_for(paused.entered.wait(),2)
    assert paused.compiler.delete_book(book.id) and paused.compiler.get_book(book.id) is None
    assert paused.compiler._compilations.get(book.id) is job, 'delete dropped cancellation handle before resource cleanup'
    await asyncio.gather(job,return_exceptions=True)
    assert paused.cleaned.is_set() and book.status.value=='error'

@pytest.mark.parametrize('exception',[False,True])
async def test_real_main_lifespan_always_stops_existing_background_services(flow,monkeypatch,tmp_path,exception):
    monkeypatch.setenv('KNOWALL_LOG_DIR',str(tmp_path/'logs'))
    import app.main as main
    import app.websocket as ws
    import app.core.agents.research_agent as research
    import app.core.book.compiler as compiler
    monkeypatch.setattr(main,'startup_init',AsyncMock())
    begun=[];stopped=AsyncMock();monkeypatch.setattr(ws,'start_background_tasks',lambda:begun.append(True))
    monkeypatch.setattr(ws,'stop_background_tasks',stopped)
    rclose=AsyncMock();bclose=AsyncMock()
    monkeypatch.setattr(research.research_agent,'close',rclose,raising=False)
    monkeypatch.setattr(compiler.book_compiler,'close',bclose,raising=False)
    async def use():
        async with main.lifespan(FastAPI()):
            if exception:raise RuntimeError('explicit isolated lifespan body failure')
    if exception:
        with pytest.raises(RuntimeError,match='lifespan body'):await use()
    else:await use()
    assert begun==[True] and stopped.await_count==rclose.await_count==bclose.await_count==1

@pytest.mark.parametrize('kind',['research','book'])
@pytest.mark.parametrize('prestart',[False,True])
async def test_close_multiple_jobs_is_terminal_idempotent_and_rejects_new_work(paused,kind,prestart):
    service=paused.agent if kind=='research' else paused.compiler
    items=[await start(paused,kind) for _ in range(3)]
    if not prestart:await asyncio.wait_for(paused.entered.wait(),2)
    await service.close();await service.close()
    assert all(i.status.value=='error' and i.error and i.progress<1 for i in items)
    assert not (service._running if kind=='research' else service._compilations)
    with pytest.raises(RuntimeError,match='shutting down'):await start(paused,kind)

@pytest.mark.parametrize('kind',['research','book'])
@pytest.mark.parametrize('failure',[False,True])
async def test_close_preserves_completed_and_failed_results(flow,kind,failure):
    if failure:flow.model.side_effect=RuntimeError('explicit simulated existing failure')
    item=await start(flow,kind)
    async with asyncio.timeout(5):
        while item.status.value not in ('completed','error'):await asyncio.sleep(.001)
    status,error,progress=item.status,item.error,item.progress
    await (flow.agent if kind=='research' else flow.compiler).close()
    assert (item.status,item.error,item.progress)==(status,error,progress)
    if kind=='research':assert item.completed_at>0

@pytest.mark.parametrize('kind,mode',[(kind,mode) for kind in ('research','book') for mode in
    ('concurrent-close','cancel-close','delete-close','repeat-delete') if kind=='book' or mode in ('concurrent-close','cancel-close')])
async def test_cleanup_is_not_interrupted_by_second_cancellation(flow,kind,mode):
    entered=asyncio.Event();cleanup=asyncio.Event();release=asyncio.Event();cleaned=asyncio.Event()
    async def simulated(prompt,**kwargs):
        entered.set()
        try:await asyncio.Event().wait()
        finally:
            cleanup.set();await release.wait();cleaned.set()
    flow.model.side_effect=simulated
    service=flow.agent if kind=='research' else flow.compiler
    item=await start(flow,kind);await asyncio.wait_for(entered.wait(),2)
    closers=[]
    try:
        if mode.startswith(('delete','repeat')):assert flow.compiler.delete_book(item.id)
        else:closers.append(asyncio.create_task(service.close()))
        await asyncio.wait_for(cleanup.wait(),2)
        if mode=='repeat-delete':assert not flow.compiler.delete_book(item.id)
        elif mode=='cancel-close':closers[0].cancel()
        else:closers.append(asyncio.create_task(service.close()))
        await asyncio.sleep(.02)
        assert not cleaned.is_set()
        assert any(not j.done() for j in (service._running if kind=='research' else service._compilations.values())), 'second cancel abandoned awaited cleanup'
        release.set()
        results=await asyncio.gather(*closers,return_exceptions=True)
        if mode=='cancel-close':assert isinstance(results[0],asyncio.CancelledError)
        await service.close()
        assert cleaned.is_set() and item.status.value=='error'
    finally:
        release.set();await asyncio.gather(*closers,return_exceptions=True)

async def test_lifespan_cleanup_error_still_attempts_every_service(flow,monkeypatch,tmp_path):
    monkeypatch.setenv('KNOWALL_LOG_DIR',str(tmp_path/'logs'))
    import app.main as main
    import app.websocket as ws
    monkeypatch.setattr(main,'startup_init',AsyncMock())
    monkeypatch.setattr(ws,'start_background_tasks',lambda:None)
    stopped=AsyncMock();monkeypatch.setattr(ws,'stop_background_tasks',stopped)
    rclose=AsyncMock(side_effect=RuntimeError('explicit simulated close failure'));bclose=AsyncMock()
    monkeypatch.setattr(flow.agent,'close',rclose);monkeypatch.setattr(flow.compiler,'close',bclose)
    with pytest.raises(RuntimeError,match='close failure'):
        async with main.lifespan(FastAPI()):pass
    assert rclose.await_count==bclose.await_count==stopped.await_count==1

@pytest.mark.parametrize('kind,needle',[('research','分解为'),('research','撰写一份结构化'),
    ('book','优秀的教材编写者'),('book','测试题'),('book','一句话概括')])
async def test_cancellation_during_later_generation_stages_keeps_partial_result_unfinished(flow,kind,needle):
    entered=asyncio.Event();cleaned=asyncio.Event();calls=[]
    async def simulated(prompt,**kwargs):
        calls.append(prompt)
        if needle in prompt:
            entered.set()
            try:await asyncio.Event().wait()
            finally:cleaned.set()
        return await flow.chat(prompt,**kwargs)
    flow.model.side_effect=simulated
    item=await start(flow,kind);await asyncio.wait_for(entered.wait(),3)
    count=len(calls);await (flow.agent if kind=='research' else flow.compiler).close()
    assert cleaned.is_set() and item.status.value=='error' and item.error and item.progress<1 and len(calls)==count
    if kind=='book':
        response=await flow.client.get(f'/api/v1/books/{item.id}/export')
        assert response.status_code==409
    else:
        events=[v async for v in flow.agent.stream_progress(item.task_id)]
        assert events[-1]['event']=='error' and item.completed_at>0

async def test_startup_failure_does_not_start_websocket_or_enter_running_lifespan(flow,monkeypatch,tmp_path):
    monkeypatch.setenv('KNOWALL_LOG_DIR',str(tmp_path/'logs'))
    import app.main as main
    import app.websocket as ws
    monkeypatch.setattr(main,'startup_init',AsyncMock(side_effect=RuntimeError('explicit isolated startup failure')))
    begun=[];stopped=AsyncMock()
    monkeypatch.setattr(ws,'start_background_tasks',lambda:begun.append(True));monkeypatch.setattr(ws,'stop_background_tasks',stopped)
    with pytest.raises(RuntimeError,match='startup failure'):
        async with main.lifespan(FastAPI()):raise AssertionError('should not enter')
    assert not begun and stopped.await_count==0
    assert not flow.agent._running and not flow.compiler._compilations

async def test_lifespan_body_cancellation_closes_actual_pending_jobs(paused,monkeypatch,tmp_path):
    monkeypatch.setenv('KNOWALL_LOG_DIR',str(tmp_path/'logs'))
    import app.main as main
    import app.websocket.sync_server as ws
    monkeypatch.setattr(main,'startup_init',AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        async with main.lifespan(FastAPI()):
            item=await start(paused,'research');await asyncio.wait_for(paused.entered.wait(),2)
            raise asyncio.CancelledError()
    assert item.status.value=='error' and paused.cleaned.is_set()
    assert not paused.agent._running and not paused.compiler._compilations and not ws._background_tasks
