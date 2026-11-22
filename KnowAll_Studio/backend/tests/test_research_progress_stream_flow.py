"""Public research stream behavior; no external service or production data."""
import asyncio

import pytest

from app.core.agents.research_agent import ResearchAgent, ResearchStatus, ResearchTask, SubTopic


def running():
    agent = ResearchAgent()
    task = ResearchTask(task_id='private', topic='模拟主题', status=ResearchStatus.RESEARCHING,
                        progress=.2, sub_topics=[SubTopic('one', '子课题一', '', status='researching')])
    agent._tasks[task.task_id] = task
    return agent, task


@pytest.mark.parametrize('change', ['progress', 'subtopic', 'topic', 'title', 'append'])
async def test_public_change_inside_same_phase_is_delivered(change):
    agent, task = running()
    stream = agent.stream_progress(task.task_id)
    first = await anext(stream)
    original = repr(first)
    if change == 'progress':
        task.progress = .4
    elif change == 'subtopic':
        task.sub_topics[0].status = 'done'
    elif change == 'topic':
        task.refined_topic = '细化模拟主题'
    elif change == 'title':
        task.sub_topics[0].title = '更新子课题'
    else:
        task.sub_topics.append(SubTopic('two', '子课题二', ''))
    try:
        update = await asyncio.wait_for(anext(stream), 1.2)
        assert update['event'] == 'progress'
        assert update['data']['status'] == first['data']['status'] == 'researching'
        assert update['data'] != first['data']
        assert repr(first) == original, 'previously delivered payload mutated with the task'
        assert update['data']['progress'] == task.progress
        assert update['data']['topic'] == (task.refined_topic or task.topic)
        assert update['data']['sub_topics'] == [dict(title=s.title, status=s.status) for s in task.sub_topics]
    finally:
        await stream.aclose()


async def test_idle_stream_suppresses_duplicates_until_public_change():
    agent, task = running()
    stream = agent.stream_progress(task.task_id)
    await anext(stream)
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(.65)
        assert not pending.done(), 'unchanged state was resent'
        task.progress = .5
        update = await asyncio.wait_for(pending, 1)
        assert update['data']['progress'] == .5
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()


async def test_subscribers_do_not_share_delivery_state_and_reconnect_sees_latest():
    agent, task = running()
    first, second = agent.stream_progress(task.task_id), agent.stream_progress(task.task_id)
    try:
        assert await anext(first) == await anext(second)
        await first.aclose()
        task.progress = .6
        update = await asyncio.wait_for(anext(second), 1.2)
        reconnect = agent.stream_progress(task.task_id)
        try:
            assert await anext(reconnect) == update
        finally:
            await reconnect.aclose()
        assert task.status == ResearchStatus.RESEARCHING
    finally:
        await second.aclose()


@pytest.mark.parametrize('terminal', ['completed', 'error'])
async def test_active_stream_ends_once_with_correct_terminal_event(terminal):
    agent, task = running()
    stream = agent.stream_progress(task.task_id)
    await anext(stream)
    task.status = ResearchStatus(terminal)
    task.report = '明确模拟报告'
    task.error = '明确模拟失败'
    try:
        event = await asyncio.wait_for(anext(stream), 1.2)
        assert event['event'] == ('complete' if terminal == 'completed' else 'error')
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()
