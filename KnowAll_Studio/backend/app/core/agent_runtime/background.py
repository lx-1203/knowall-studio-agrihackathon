"""In-worker task supervision and durable stop observation, without crash replay."""
import asyncio
import logging
from . import store

logger=logging.getLogger('knowall.agent.background')


class BackgroundRunManager:
    def __init__(self, *, poll_seconds=.1):
        self.poll_seconds=poll_seconds
        self._jobs={}
        self._submissions=set()
        self.accepting=True
        self._closing=None

    def activate(self):
        """Reopen only after the preceding owner drain has fully finished."""
        if self._jobs or self._submissions or (self._closing is not None and not self._closing.done()):
            raise RuntimeError('Background manager is still draining')
        self._closing=None
        self.accepting=True

    def submit(self,factory):
        """Accept a coroutine factory synchronously; own it beyond HTTP loss."""
        if not self.accepting:raise RuntimeError('Background manager is shutting down')
        task=asyncio.create_task(factory(),name='agent-background-submission')
        self._submissions.add(task)
        task.add_done_callback(self._submitted)
        return task

    def _submitted(self,task):
        self._submissions.discard(task)
        # Retrieve detached HTTP errors without exposing credential/SQL details.
        if not task.cancelled():task.exception()

    def launch(self,run_id,user_id,factory,execute):
        if not self.accepting and asyncio.current_task() not in self._submissions:
            raise RuntimeError('Background manager is shutting down')
        if run_id in self._jobs:raise RuntimeError('Background run already registered')
        # execute is a factory, so no coroutine leaks if registration fails.
        task=asyncio.create_task(self._supervise(run_id,user_id,factory,execute),name='agent-background-'+run_id)
        self._jobs[run_id]={'supervisor':task,'work':None,'started':False,'cancelled':not self.accepting}
        task.add_done_callback(lambda done:self._finished(run_id,done))

    def _finished(self,run_id,task):
        self._jobs.pop(run_id,None)
        if not task.cancelled():
            error=task.exception()
            if error is not None:
                # Error details may contain model/database context; do not
                # serialize them into receipts or client responses.
                logger.error('Background run supervision failed (%s)',type(error).__name__)

    def cancel(self,run_id):
        job=self._jobs.get(run_id)
        if job and not job['cancelled']:
            job['cancelled']=True
            work=job['work']
            if job['started'] and work is not None and not work.done() and not work.cancelling():work.cancel()

    async def _supervise(self,run_id,user_id,factory,execute):
        job=self._jobs[run_id]
        async def run():
            job['started']=True
            if job['cancelled']:asyncio.current_task().cancel()
            return await execute()
        work=asyncio.create_task(run(),name='agent-executor-'+run_id)
        job['work']=work
        finished=asyncio.Event()
        async def watch():
            while not work.done():
                try:await store.check_background_stop(factory,run_id,user_id)
                except asyncio.CancelledError:
                    self.cancel(run_id);return
                except Exception:
                    # The executor independently checks each action boundary;
                    # a transient watcher read does not invent a stop request.
                    pass
                try:await asyncio.wait_for(finished.wait(),self.poll_seconds)
                except TimeoutError:pass
        watcher=asyncio.create_task(watch(),name='agent-stop-watcher-'+run_id)
        try:
            await work
        finally:
            # Let an in-flight SQL read close normally. Cancelling the reader
            # during driver cleanup can leave SQLite readers holding locks.
            finished.set()
            if not work.done():self.cancel(run_id)
            await asyncio.gather(watcher,work,return_exceptions=True)

    async def close(self):
        """Close admission first; one shielded drain owns all accepted work."""
        self.accepting=False
        if self._closing is None:
            self._closing=asyncio.create_task(self._drain(),name='agent-background-drain')
        await asyncio.shield(self._closing)

    async def _drain(self):
        # Accepted submissions may still be committing SQL before launch. No
        # new submission can join this set once close has closed admission.
        await asyncio.gather(*list(self._submissions),return_exceptions=True)
        jobs=list(self._jobs.items())
        for run_id,_ in jobs:self.cancel(run_id)
        await asyncio.gather(*(job['supervisor'] for _,job in jobs),return_exceptions=True)


class StopAwareAPI:
    def __init__(self,api,check):self.api=api;self.check=check
    async def agent_turn(self,*args,**kwargs):
        await self.check()
        return await self.api.agent_turn(*args,**kwargs)


class StopAwareBridge:
    def __init__(self,bridge,check):self.bridge=bridge;self.check=check;self.user_id=bridge.user_id
    def definitions(self):return self.bridge.definitions()
    async def execute(self,*args):
        await self.check()
        return await self.bridge.execute(*args)
    async def verified_sources(self,*args):
        await self.check()
        return await self.bridge.verified_sources(*args)
