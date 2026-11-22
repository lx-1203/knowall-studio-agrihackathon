"""Bounded read-only answers with SQL terminal receipts, without crash replay."""
import asyncio,copy,json
import anyio
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select,or_
from sqlalchemy.exc import SQLAlchemyError
from app.database import get_db
from app.core.auth import get_current_user,load_user_api_keys
from app.core.api_scheduler import api_client
from app.core.api_scheduler.client import QuotaExceededError
from app.core.api_scheduler.adapters.base import BaseModelAdapter
from app.core.agent_runtime.tool_bridge import DocumentToolBridge
from app.core.agent_runtime.runner import run_readonly
from app.core.agent_runtime.contracts import AgentExecutionError
from app.core.agent_runtime import store
from app.core.agent_runtime import input_checkpoint
from app.core.agent_runtime.capabilities import runtime_capabilities
from app.core.agent_runtime.background import BackgroundRunManager,StopAwareAPI,StopAwareBridge
from app.api.chat import ChatRequest,_get_or_create_conversation,_get_history
from app.models import Message,Conversation,AgentRun,AgentBackgroundTask

router=APIRouter(prefix='/api/v1/agent',tags=['native-learning-agent'])
background_manager=BackgroundRunManager()


class AgentAnswerRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    message:str=Field(min_length=1,max_length=8000)
    model:str=Field(default='deepseek-chat',min_length=1,max_length=256)
    conversation_id:str|None=Field(default=None,min_length=1,max_length=36)

    @field_validator('message','model')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('cannot be blank')
        return value.strip()


class BackgroundAnswerRequest(AgentAnswerRequest):
    persist_input:bool=False


@router.get('/capabilities')
async def capabilities(model:str=Query(default='deepseek-chat',min_length=1,max_length=256),
                       db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    user_id=current_user.id
    try:
        await load_user_api_keys(user_id,db,True)
        await db.rollback()
        runtime=await runtime_capabilities(db,accepting_background=background_manager.accepting)
    except SQLAlchemyError as error:
        await db.rollback()
        raise HTTPException(503,'运行能力暂时无法核对，请重试') from error
    from app.database import async_session
    bridge=DocumentToolBridge(async_session,user_id)
    try:
        adapter=api_client.get_agent_adapter(model,user_id)
        available=type(adapter).chat_completion_with_tools is not BaseModelAdapter.chat_completion_with_tools
    except ValueError:available=False
    return {'available':available,'scope':'readonly_documents','model':model,
            'model_tool_support':'unverified',**runtime,
            'tools':bridge.definitions() if available else [],
            'limits':{'model_turns':4,'tool_calls':6,'run_seconds':90,'tool_seconds':5},
            'reason':None if available else '请为当前账号配置支持原生工具协议的模型'}


@router.get('/models')
async def models(db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user),refresh:bool=False):
    user_id=current_user.id
    try:
        await load_user_api_keys(user_id,db,True)
        from app.core.model_connections import effective_default, metadata, TAG
        from app.models import APIKey
        selected_model,_=await effective_default(db,user_id)
        connection_keys=(await db.scalars(select(APIKey).where(APIKey.user_id==user_id,APIKey.permission_level==TAG))).all()
        connection_names={'connection-'+key: value['name'] for key,value in (await metadata(db,connection_keys)).items()}
        await db.rollback()
        runtime=await runtime_capabilities(db,accepting_background=background_manager.accepting)
    except SQLAlchemyError as error:
        await db.rollback()
        raise HTTPException(503,'运行能力暂时无法核对，请重试') from error
    if refresh:
        from app.core.model_catalog import discover_models
        result=await discover_models(api_client,user_id)
    else:
        result={'models':api_client.get_agent_models(user_id),'source':'configured'}
    for item in result['models']:
        provider,_,model=item['value'].partition('/')
        if provider in connection_names:
            item['label']=connection_names[provider]+' / '+(item.get('display_name') or model)
    return {**result,'default_model':selected_model,**runtime}


@router.get('/sources/{chunk_id}')
async def source(chunk_id:str,sha256:str=Query(pattern=r'^[0-9a-fA-F]{64}$'),
                 db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    user_id=current_user.id
    await db.rollback()
    from app.database import async_session
    bridge=DocumentToolBridge(async_session,user_id)
    result=await bridge.execute('source','read_document_chunk',{'chunk_id':chunk_id})
    if result.status!='success':
        code=result.output.get('error')
        if code in ('SOURCE_NOT_FOUND','INVALID_ARGUMENTS'):
            raise HTTPException(404,'来源不存在或当前账号无权访问')
        raise HTTPException(503,'来源暂时无法读取，请重试')
    current=result.output['sources'][0]
    if current['chunk_sha256']!=sha256.lower():
        raise HTTPException(409,'资料内容已变化，请重新提问获取当前来源')
    return current


@router.post('/answer')
async def answer(req:AgentAnswerRequest,request:Request,db:AsyncSession=Depends(get_db),
                 current_user=Depends(get_current_user)):
    # FastAPI has already consumed/validated the JSON body. This is the sole
    # receive consumer from here on; polling is_disconnected concurrently could
    # steal ASGI messages. Keep the listener alive through final persistence.
    disconnected=asyncio.Event()
    work=asyncio.create_task(_answer_connected(req,db,current_user,disconnected))
    async def watch_disconnect():
        while True:
            if (await request.receive())['type']=='http.disconnect':
                disconnected.set()
                work.cancel()
                return
    watcher=asyncio.create_task(watch_disconnect())
    try:
        return await work
    except asyncio.CancelledError:
        if not disconnected.is_set():
            raise
        # The socket has gone; this status only describes an incomplete local
        # request. urllib threads/already-started provider billing may continue.
        return Response(status_code=499)
    finally:
        watcher.cancel()
        if not work.done():work.cancel()
        await asyncio.gather(watcher,work,return_exceptions=True)


async def _controls(factory,run_ids,user_id):
    # Existing request-mode receipts remain readable before the optional
    # background migration. Background submission itself always requires it.
    async with factory() as db:
        try:
            rows=(await db.scalars(select(AgentBackgroundTask).where(
                AgentBackgroundTask.run_id.in_(run_ids),AgentBackgroundTask.user_id==user_id))).all()
            return {row.run_id:row for row in rows}
        except SQLAlchemyError as error:
            missing=str(error).lower()
            if 'agent_background_tasks' in missing and any(marker in missing for marker in (
                    'no such table','does not exist',"doesn't exist",'undefinedtable')):
                return {}
            raise


async def _input_metadata(factory,run_ids,user_id):
    if not run_ids:return set()
    async with factory() as db:
        try:return await input_checkpoint.metadata(db,run_ids,user_id)
        except SQLAlchemyError as error:
            if input_checkpoint.is_missing_table(error):return set()
            raise


@router.get('/runs')
async def list_runs(limit:int=Query(default=20,ge=1,le=50),offset:int=Query(default=0,ge=0),
                    db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    user_id=current_user.id
    try:
        rows=(await db.scalars(select(AgentRun).outerjoin(Conversation,Conversation.id==AgentRun.conversation_id).where(
            AgentRun.user_id==user_id,or_(AgentRun.conversation_id.is_(None),Conversation.user_id==user_id))
            .order_by(AgentRun.created_at.desc(),AgentRun.id.desc()).offset(offset).limit(limit+1))).all()
        from app.database import async_session
        run_ids=[row.id for row in rows[:limit]]
        controls=await _controls(async_session,run_ids,user_id) if rows else {}
        saved=await _input_metadata(async_session,run_ids,user_id)
        return {'runs':[store.background_receipt(row,controls.get(row.id),input_saved=row.id in saved)
                       for row in rows[:limit]],'has_more':len(rows)>limit}
    except SQLAlchemyError as error:
        raise HTTPException(503,'运行记录暂时无法读取，请重试') from error


@router.post('/runs',status_code=202)
async def start_background(req:BackgroundAnswerRequest,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    from types import SimpleNamespace
    from app.database import async_session
    # Snapshot trusted scalar inputs only; never retain a request db/user ORM
    # instance in the executor. Opt-in input snapshots contain no credentials.
    snapshot=req.model_copy(deep=True);user_id=current_user.id;manager=background_manager;api=api_client
    async def submit():
        # Admission precedes all model/history preflight. This session belongs
        # to the accepted submission, so HTTP dependency cleanup cannot race it.
        try:
            async with async_session() as preparation_db:
                _,chat_req,history=await _prepare_answer(snapshot,preparation_db,SimpleNamespace(id=user_id))
        except SQLAlchemyError as error:
            raise HTTPException(503,'运行记录暂时无法保存；任务未启动') from error
        history=copy.deepcopy(history)
        try:run_id=await store.start_background(async_session,user_id,snapshot.conversation_id,snapshot.model,
            checkpoint_input={'message':snapshot.message,'history':history} if snapshot.persist_input else None)
        except (SQLAlchemyError,RuntimeError) as error:
            raise HTTPException(503,'运行记录暂时无法保存；任务未启动') from error
        try:
            manager.launch(run_id,user_id,async_session,lambda:_execute_background(
                run_id,user_id,snapshot,chat_req,history,async_session,api))
        except Exception as error:
            await store.terminate(async_session,run_id,user_id,'failed','PERSISTENCE_FAILED')
            raise HTTPException(503,'后台任务暂时无法登记；请先查询运行列表') from error
        return {'run_id':run_id,'execution_mode':'background','input_saved':snapshot.persist_input}
    # Submission is independent from a lost response connection. No auto
    # retry is safe if the client cannot determine whether 202 was received.
    try:submission=manager.submit(submit)
    except RuntimeError as error:
        raise HTTPException(503,'服务正在关闭，任务未启动') from error
    return await asyncio.shield(submission)


@router.get('/runs/{run_id}')
async def get_run(run_id:str,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    try:
        row=await _owned_run(run_id,db,current_user.id)
        from app.database import async_session
        controls=await _controls(async_session,[run_id],current_user.id)
        saved=await _input_metadata(async_session,[run_id],current_user.id)
        return store.background_receipt(row,controls.get(run_id),input_saved=run_id in saved)
    except SQLAlchemyError as error:
        raise HTTPException(503,'运行记录暂时无法读取，请重试') from error


@router.get('/runs/{run_id}/input')
async def get_input(run_id:str,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    try:
        row=await _owned_run(run_id,db,current_user.id)
        cp,payload=await input_checkpoint.load_owned(db,row,current_user.id)
        if cp is None:raise HTTPException(404,'任务输入未保存或已删除')
        from datetime import timezone
        return {'run_id':run_id,'message':payload.message,'requested_model':payload.requested_model,
            'created_at':cp.created_at.replace(tzinfo=timezone.utc).isoformat(),'version':cp.version}
    except input_checkpoint.InputNotOwned as error:
        raise HTTPException(404,'任务输入不存在或当前账号无权访问') from error
    except input_checkpoint.InputUnavailable as error:
        raise HTTPException(503,'任务输入暂时无法解密，请恢复原密钥后重试') from error
    except SQLAlchemyError as error:
        if input_checkpoint.is_missing_table(error):raise HTTPException(404,'任务输入未保存或已删除') from error
        raise HTTPException(503,'任务输入暂时无法读取，请重试') from error


@router.delete('/runs/{run_id}/input',status_code=204)
async def delete_input(run_id:str,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    try:
        row=await _owned_run(run_id,db,current_user.id)
        cp,payload=await input_checkpoint.load_owned(db,row,current_user.id)
        if cp is not None:
            await db.delete(cp)
            await db.commit()
        return Response(status_code=204)
    except input_checkpoint.InputNotOwned as error:
        raise HTTPException(404,'任务输入不存在或当前账号无权访问') from error
    except input_checkpoint.InputUnavailable as error:
        raise HTTPException(503,'任务输入暂时无法核对权限，请恢复原密钥后重试') from error
    except SQLAlchemyError as error:
        await db.rollback()
        if input_checkpoint.is_missing_table(error):return Response(status_code=204)
        raise HTTPException(503,'任务输入暂时无法删除，请重试') from error


@router.get('/runs/{run_id}/result')
async def get_result(run_id:str,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    row=await _owned_run(run_id,db,current_user.id)
    if row.status!='completed':raise HTTPException(409,'运行尚无成功结果；请查看真实状态')
    message=await db.scalar(select(Message).where(Message.id==row.assistant_message_id,
        Message.conversation_id==row.conversation_id,Message.role=='assistant'))
    if message is None:raise HTTPException(409,'运行结果消息暂时无法读取')
    return {'status':'completed','scope':row.scope,'conversation_id':row.conversation_id,
            'run_id':row.id,'assistant_message_id':message.id,'message':message.content,
            'sources':store.receipt(row)['sources'],'steps':copy.deepcopy(row.steps or []),'model':row.model,
            'usage':{'input_tokens':message.tokens_input or 0,'output_tokens':message.tokens_output or 0}}


@router.post('/runs/{run_id}/cancel',status_code=202)
async def cancel_background(run_id:str,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    user_id=current_user.id
    await _owned_run(run_id,db,user_id)
    from app.database import async_session
    if run_id not in await _controls(async_session,[run_id],user_id):
        raise HTTPException(409,'该运行与原请求关联；不支持后台停止')
    # Release JWT/ownership read snapshots before acquiring a write lock.
    # Otherwise a simultaneous executor commit can deadlock a SQLite read-to-
    # write upgrade. Ownership is checked again while holding the control lock.
    await db.rollback()
    try:
        control=await store.lock_background_control(db,run_id,user_id)
        row=await _owned_run(run_id,db,user_id)
        control.cancel_requested=True
        await db.commit()
    except (SQLAlchemyError,RuntimeError) as error:
        await db.rollback()
        raise HTTPException(503,'停止请求暂时无法保存；请重试并查看真实状态') from error
    # This is a persisted request acknowledgement, never a fabricated terminal.
    # Repeat stops cannot interrupt an already-running tool finally block.
    background_manager.cancel(run_id)
    return {'run_id':run_id,'status':row.status,'cancel_requested':True}


async def _owned_run(run_id,db,user_id):
    row=await db.scalar(select(AgentRun).where(AgentRun.id==run_id,AgentRun.user_id==user_id))
    if row is None:raise HTTPException(404,'运行记录不存在或当前账号无权访问')
    if row.conversation_id:
        conv=await db.scalar(select(Conversation.id).where(Conversation.id==row.conversation_id,Conversation.user_id==user_id))
        if conv is None:raise HTTPException(404,'运行记录不存在或当前账号无权访问')
    return row


@router.get('/runs/{run_id}/events')
async def get_events(run_id:str,after:int=Query(default=0,ge=0),db:AsyncSession=Depends(get_db),
                     current_user=Depends(get_current_user)):
    row=await _owned_run(run_id,db,current_user.id)
    events=row.steps or []
    return {'run_id':row.id,'status':row.status,'events':[item for item in events if item.get('seq',0)>after],
            'last_seq':events[-1]['seq'] if events else 0,'recovery_state':store.observation(row)['recovery_state']}


class _AgentStreamingResponse(StreamingResponse):
    """Starlette is the only receive listener; cleanup outlives its cancel scope."""
    def __init__(self,content,work,disconnected):
        super().__init__(content,media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
        self.work=work;self.disconnected=disconnected

    async def __call__(self,scope,receive,send):
        try:
            await super().__call__(scope,receive,send)
        finally:
            with anyio.CancelScope(shield=True):
                if not self.work.done():
                    self.disconnected.set();self.work.cancel()
                await asyncio.gather(self.work,return_exceptions=True)


@router.post('/answer/stream')
async def answer_stream(req:AgentAnswerRequest,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    # Authentication, input validation, current conversation ownership and model
    # selection all happen before response headers switch to an SSE stream.
    prepared=await _prepare_answer(req,db,current_user)
    queue=asyncio.Queue();disconnected=asyncio.Event();observed={'steps':[]}
    async def publish(item):
        observed['run_id']=item['run_id']
        if item['kind']=='step':observed['steps'].append(item['event'])
        await queue.put(item)
    async def execute():
        try:
            result=await _answer_connected(req,db,current_user,disconnected,prepared=prepared,publish=publish)
            await publish({'kind':'result','run_id':result['run_id'],'result':result})
        except asyncio.CancelledError:
            raise
        except HTTPException as error:
            code=error.detail.get('code') if isinstance(error.detail,dict) else {
                429:'QUOTA_EXCEEDED',404:'CONVERSATION_UNAVAILABLE',503:'PERSISTENCE_FAILED'}.get(error.status_code,'MODEL_REQUEST_FAILED')
            await queue.put({'kind':'error',**({'run_id':observed['run_id']} if 'run_id' in observed else {}),
                             'code':code,'steps':observed['steps']})
        except Exception:
            await queue.put({'kind':'error',**({'run_id':observed['run_id']} if 'run_id' in observed else {}),
                             'code':'PERSISTENCE_FAILED','steps':observed['steps']})
        finally:
            await queue.put(None)
    work=asyncio.create_task(execute())
    async def packets():
        while True:
            item=await queue.get()
            if item is None:return
            yield 'data: '+json.dumps(item,ensure_ascii=False)+'\n\n'
    return _AgentStreamingResponse(packets(),work,disconnected)


@router.get('/conversations/{conv_id}/receipts')
async def conversation_receipts(conv_id:str,db:AsyncSession=Depends(get_db),current_user=Depends(get_current_user)):
    conv=await db.scalar(select(Conversation.id).where(Conversation.id==conv_id,Conversation.user_id==current_user.id))
    if conv is None:raise HTTPException(404,'会话不存在或当前账号无权访问')
    rows=(await db.scalars(select(AgentRun).join(Message,Message.id==AgentRun.assistant_message_id).where(
        AgentRun.user_id==current_user.id,AgentRun.conversation_id==conv_id,AgentRun.status=='completed',
        Message.conversation_id==conv_id,Message.role=='assistant').order_by(AgentRun.created_at.desc(),AgentRun.id.desc()).limit(101))).all()
    return {'receipts':[store.receipt(row) for row in rows[:100]],'has_more':len(rows)>100}


async def _prepare_answer(req,db,current_user):
    user_id=current_user.id
    chat_req=ChatRequest(message=req.message,conversation_id=req.conversation_id,role_preset='tutor',model=req.model)
    history=[]
    if req.conversation_id:
        conv=await _get_or_create_conversation(db,chat_req,user_id)
        history=await _get_history(db,conv.id)
    await db.rollback()
    await load_user_api_keys(user_id,db,True)
    await db.rollback()
    try:
        api_client.get_agent_adapter(req.model,user_id)
    except ValueError:
        raise HTTPException(400,'请先为当前账号配置该模型；不会使用其他账号的模型凭据')
    return user_id,chat_req,history


async def _execute_background(run_id,user_id,req,chat_req,history,factory,api):
    async def check():await store.check_background_stop(factory,run_id,user_id)
    bridge=StopAwareBridge(DocumentToolBridge(factory,user_id),check)
    async def sink(item):
        await check()
        if item['type'] in ('run_completed','run_failed'):return
        saved=await store.append_event(factory,run_id,user_id,item)
        item.clear();item.update(saved)
    async with factory() as db:
        try:
            await check()
            result=await run_readonly(StopAwareAPI(api,check),bridge,req.message,req.model,history,event_sink=sink)
            await check()
            await store.lock_background_completion(db,run_id,user_id)
            # This fresh executor session has never loaded a Conversation.
            # Re-read current ownership after all model/tool awaits.
            conv=await _get_or_create_conversation(db,chat_req,user_id)
            user_message=Message(conversation_id=conv.id,role='user',content=req.message)
            assistant_message=Message(conversation_id=conv.id,role='assistant',content=result.content,
                model_used=result.model,tokens_input=result.tokens_input,tokens_output=result.tokens_output)
            from uuid import uuid4
            assistant_message.id=str(uuid4())
            db.add_all([user_message,assistant_message])
            await store.complete(db,run_id,user_id,conv.id,assistant_message.id,result)
            await db.commit()
        except asyncio.CancelledError:
            await db.rollback()
            # Cancellation propagation has already awaited the active model or
            # tool finally; only now may the durable terminal be written.
            await store.terminate(factory,run_id,user_id,'cancelled','BACKGROUND_CANCELLED')
            raise
        except QuotaExceededError:
            await db.rollback()
            await store.terminate(factory,run_id,user_id,'failed','QUOTA_EXCEEDED')
        except AgentExecutionError as error:
            await db.rollback()
            await store.terminate(factory,run_id,user_id,'failed',error.code)
        except HTTPException:
            await db.rollback()
            await store.terminate(factory,run_id,user_id,'failed','CONVERSATION_UNAVAILABLE')
        except Exception:
            await db.rollback()
            await store.terminate(factory,run_id,user_id,'failed','PERSISTENCE_FAILED')


async def _answer_connected(req,db,current_user,disconnected,*,prepared=None,publish=None):
    user_id,chat_req,history=prepared or await _prepare_answer(req,db,current_user)
    from app.database import async_session
    bridge=DocumentToolBridge(async_session,user_id)
    # A committed running row must still obtain a terminal cancellation when
    # the socket closes during its initial commit or before start returns.
    starting=asyncio.create_task(store.start(async_session,user_id,req.conversation_id,req.model))
    try:
        run_id=await asyncio.shield(starting)
    except asyncio.CancelledError:
        try:
            run_id=await starting
            await store.terminate(async_session,run_id,user_id,'cancelled','REQUEST_CANCELLED')
        finally:
            if not starting.done():starting.cancel()
            await asyncio.gather(starting,return_exceptions=True)
        raise
    except (SQLAlchemyError,RuntimeError) as error:
        raise HTTPException(503,'运行记录暂时无法保存；任务未启动') from error
    last_published=0
    async def publish_steps(events):
        nonlocal last_published
        for item in events:
            if item['seq']<=last_published:continue
            if item['seq']!=last_published+1:raise RuntimeError('Non-contiguous committed events')
            await publish({'kind':'step','run_id':run_id,'event':item})
            last_published=item['seq']
    async def sink(item):
        # Runner records terminal proposals in its result but publishes neither
        # terminal until the appropriate SQL transaction has committed.
        if item['type'] in ('run_completed','run_failed'):return
        saved=await store.append_event(async_session,run_id,user_id,item)
        item.clear();item.update(saved)
        await publish_steps([saved])
    async def terminate(status,code,steps=None):
        saved=await store.terminate(async_session,run_id,user_id,status,code,None if publish else steps)
        if publish and saved:
            # Startup may have committed even when the first sink read failed.
            # Send its committed prefix before the terminal, never seq 2 first.
            await publish_steps(saved)
    try:
        result=await run_readonly(api_client,bridge,req.message,req.model,history,event_sink=sink if publish else None)
        # Let a ready disconnect notification run before persistence boundaries.
        await asyncio.sleep(0)
        if disconnected.is_set():raise asyncio.CancelledError
        conv=await _get_or_create_conversation(db,chat_req,user_id)
        user_message=Message(conversation_id=conv.id,role='user',content=req.message)
        assistant_message=Message(conversation_id=conv.id,role='assistant',content=result.content,
                                  model_used=result.model,tokens_input=result.tokens_input,tokens_output=result.tokens_output)
        db.add_all([user_message,assistant_message])
        # UUID assigned explicitly so the final commit remains the single atomic
        # boundary; no message flush is needed to associate the receipt.
        from uuid import uuid4
        assistant_message.id=str(uuid4())
        await store.complete(db,run_id,user_id,conv.id,assistant_message.id,result)
        await asyncio.sleep(0)
        if disconnected.is_set():raise asyncio.CancelledError
        await db.commit()
        conv_id=conv.id;assistant_id=assistant_message.id
        if publish:await publish_steps(result.steps)
    except asyncio.CancelledError:
        await db.rollback()
        await terminate('cancelled','REQUEST_CANCELLED')
        raise
    except QuotaExceededError:
        await db.rollback()
        await terminate('failed','QUOTA_EXCEEDED')
        raise HTTPException(429,'模型用量不足；工具任务未完成')
    except AgentExecutionError as error:
        await db.rollback()
        await terminate('failed',error.code,error.steps)
        raise HTTPException(502,{'code':error.code,'steps':error.steps,'run_id':run_id})
    except HTTPException as error:
        await db.rollback()
        await terminate('failed','CONVERSATION_UNAVAILABLE')
        raise error
    except Exception as error:
        await db.rollback()
        await terminate('failed','PERSISTENCE_FAILED')
        raise HTTPException(503,'运行结果暂时无法保存；未提交成功回答') from error
    return {'status':'completed','scope':'readonly_documents','conversation_id':conv_id,
            'run_id':run_id,'assistant_message_id':assistant_id,
            'message':result.content,'sources':result.sources,'steps':result.steps,
            'model':result.model,'usage':{'input_tokens':result.tokens_input,'output_tokens':result.tokens_output}}
