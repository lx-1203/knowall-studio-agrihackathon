"""SQL event/receipt boundaries; observation never resumes or mutates a run."""
import copy
import asyncio
from datetime import datetime,timezone,timedelta
from sqlalchemy import select,update
from app.models.agent_run import AgentRun,utc_now
from app.models.agent_background_task import AgentBackgroundTask


async def start(factory,user_id,conversation_id,model):
    async with factory() as db:
        row=AgentRun(user_id=user_id,conversation_id=conversation_id,status='running',model=model,
                     sources=[],steps=[{'seq':1,'type':'run_started','scope':'readonly_documents',
                                       'at':datetime.now(timezone.utc).isoformat()}])
        db.add(row);await db.commit()
        return row.id


async def start_background(factory,user_id,conversation_id,model,*,checkpoint_input=None):
    from uuid import uuid4
    run_id=str(uuid4())
    async with factory() as db:
        db.add(AgentRun(id=run_id,user_id=user_id,conversation_id=conversation_id,status='running',model=model,
                       sources=[],steps=[{'seq':1,'type':'run_started','scope':'readonly_documents',
                                         'at':datetime.now(timezone.utc).isoformat()}]))
        db.add(AgentBackgroundTask(run_id=run_id,user_id=user_id,cancel_requested=False))
        if checkpoint_input is not None:
            from .input_checkpoint import create_checkpoint
            db.add(create_checkpoint(run_id,user_id,conversation_id,model,
                checkpoint_input['message'],checkpoint_input['history']))
        await db.commit()
    return run_id


async def background_control(db,run_id,user_id):
    return await db.scalar(select(AgentBackgroundTask).where(AgentBackgroundTask.run_id==run_id,
                                                            AgentBackgroundTask.user_id==user_id))


async def check_background_stop(factory,run_id,user_id):
    async with factory() as db:
        row=await background_control(db,run_id,user_id)
        if row is None:raise RuntimeError('Background control unavailable')
        if row.cancel_requested:raise asyncio.CancelledError


async def lock_background_control(db,run_id,user_id):
    # Acquire the row's write lock before reading its flag. The same row is
    # locked by the stop transaction; whichever commits first is observable.
    # The no-op UPDATE also serializes this boundary on disposable SQLite.
    changed=await db.execute(update(AgentBackgroundTask).where(
        AgentBackgroundTask.run_id==run_id,AgentBackgroundTask.user_id==user_id).values(
        cancel_requested=AgentBackgroundTask.cancel_requested))
    if changed.rowcount!=1:raise RuntimeError('Background control unavailable')
    return await background_control(db,run_id,user_id)


async def lock_background_completion(db,run_id,user_id):
    row=await lock_background_control(db,run_id,user_id)
    if row.cancel_requested:raise asyncio.CancelledError


def background_receipt(row,control=None,*,input_saved=False):
    return {**receipt(row),'execution_mode':'background' if control is not None else 'request',
            'cancel_requested':bool(control and control.cancel_requested),'input_saved':bool(input_saved)}


async def append_event(factory,run_id,user_id,item):
    """Return the canonical committed event. Startup has already committed seq 1."""
    async with factory() as db:
        row=await db.scalar(select(AgentRun).where(AgentRun.id==run_id,AgentRun.user_id==user_id,AgentRun.status=='running'))
        if row is None:raise RuntimeError('Agent run no longer running')
        events=copy.deepcopy(row.steps or [])
        if item['seq']==1 and item['type']=='run_started' and len(events)==1:
            return events[0]
        if item['seq']!=len(events)+1 or len(events)>=30 or item['type'] in ('run_completed','run_failed','run_cancelled'):
            raise RuntimeError('Invalid intermediate event')
        events.append(copy.deepcopy(item));row.steps=events
        await db.commit()
        return copy.deepcopy(item)


async def complete(db,run_id,user_id,conversation_id,assistant_message_id,result):
    row=await db.scalar(select(AgentRun).where(AgentRun.id==run_id,AgentRun.user_id==user_id,AgentRun.status=='running'))
    if row is None:raise RuntimeError('Agent run no longer running')
    row.status='completed';row.conversation_id=conversation_id;row.assistant_message_id=assistant_message_id
    row.sources=copy.deepcopy(result.sources);row.steps=copy.deepcopy(result.steps);row.model=result.model
    row.completed_at=utc_now()
    # The caller commits this row together with its conversation and messages.


async def terminate(factory,run_id,user_id,status,code,steps=None):
    async with factory() as db:
        row=await db.scalar(select(AgentRun).where(AgentRun.id==run_id,AgentRun.user_id==user_id,AgentRun.status=='running'))
        if row is None:return
        events=copy.deepcopy(steps or row.steps or [])
        events=[item for item in events if item.get('type')!='run_completed']
        terminal='run_cancelled' if status=='cancelled' else 'run_failed'
        if not events or events[-1].get('type')!=terminal:
            item={'seq':len(events)+1,'type':terminal,'code':code}
            # New event streams have UTC times; old no-sink answer steps retain
            # their original shape, including historical missing timestamps.
            if events and 'at' in events[0]:item['at']=datetime.now(timezone.utc).isoformat()
            events.append(item)
        row.status=status;row.error_code=code;row.steps=events;row.sources=[];row.completed_at=utc_now()
        await db.commit()
        return copy.deepcopy(events)


def observation(row):
    def utc(value):
        if value is None:return None
        if isinstance(value,str):
            try:value=datetime.fromisoformat(value.replace('Z','+00:00'))
            except (ValueError,TypeError):return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    created=utc(row.created_at)
    last=utc((row.steps or [{}])[-1].get('at'))
    state='terminal' if row.status in ('completed','partial','failed','cancelled') else 'active'
    if row.status in ('running','queued') and (last or created) is not None:
        if datetime.now(timezone.utc)-(last or created)>timedelta(minutes=5):state='unconfirmed'
    return {'recovery_state':state,'created_at':created.isoformat() if created else None,
            'last_event_at':last.isoformat() if last else None}


def receipt(row):
    sources=copy.deepcopy(row.sources or [])
    for source in sources:source['text']=''
    return {'run_id':row.id,'conversation_id':row.conversation_id,'assistant_message_id':row.assistant_message_id,
            'status':row.status,'scope':row.scope,'sources':sources,'steps':copy.deepcopy(row.steps or []),
            'model':row.model,'error_code':row.error_code,**observation(row)}
