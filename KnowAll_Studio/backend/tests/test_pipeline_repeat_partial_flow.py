"""Real disposable SQLite + signed accounts; all model content is simulated."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock
from pathlib import Path

import pytest
from sqlalchemy import select, func, event

from app.core.pipeline import pipeline, PipelineStage
from app.core.agents.orchestrator import orchestrator
from app.core.agents.base import AgentRegistry, BaseAgent, AgentResult
from app.core.api_scheduler import get_current_user_id, set_current_user_id
from app.models import KnowledgeTree, Outline, KnowledgeSummary, StudyPlan, QuestionBank, Flashcard, Deck
from tests.test_study_notification_flow import flow
from tests.test_pipeline_lifecycle_flow import lifecycle, request, localhost


async def collect(**kwargs):
    return [copy.deepcopy(state) async for state in pipeline.run_full_chain('source', question_count=5, card_count=5, **kwargs)]


async def count(flow, model):
    async with flow.factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_three_explicit_runs_have_valid_new_results(lifecycle):
    results = []
    for _ in range(3):
        response = await lifecycle.client.post('/api/v1/pipeline/run', headers=lifecycle.alice, json=request())
        assert response.status_code == 200, response.text
        results.append(response.json())
    assert all(result['status'] == 'completed' for result in results)
    assert len({result['tree_id'] for result in results}) == 3
    assert len({result['summary_id'] for result in results}) == 3
    assert await count(lifecycle, QuestionBank) == 15
    assert await count(lifecycle, StudyPlan) == 3


async def test_duplicate_legacy_keys_cannot_break_a_new_run(lifecycle):
    key = 'pipeline:source:single_choice:medium:qa'
    async with lifecycle.factory() as db:
        db.add_all([KnowledgeTree(name='old1', generation_cache_key=key), KnowledgeTree(name='old2', generation_cache_key=key)])
        await db.commit()
    response = await lifecycle.client.post('/api/v1/pipeline/run', headers=lifecycle.alice, json=request())
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'completed'
    assert await count(lifecycle, KnowledgeTree) == 3


@pytest.mark.parametrize('stream', [False, True])
async def test_actual_generated_card_count_is_reported(lifecycle, stream):
    lifecycle.cards.return_value = [{'front':'模拟卡片', 'back':'模拟答案'}] * 2
    response = await lifecycle.client.post('/api/v1/pipeline/run' + ('/stream' if stream else ''), headers=lifecycle.alice, json=request())
    assert response.status_code == 200
    data = response.json() if not stream else [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')][-1]['result']
    assert data['card_count'] == 2
    assert await count(lifecycle, Flashcard) == 2
    async with lifecycle.factory() as db:
        assert (await db.scalar(select(Deck))).card_count == 2


@pytest.mark.parametrize('empty_stage', ['quiz', 'cards'])
async def test_empty_generation_is_error_without_empty_success(lifecycle, empty_stage):
    getattr(lifecycle, empty_stage).return_value = []
    states = await collect(user_id='alice')
    assert states[-1].stage == PipelineStage.ERROR
    assert states[-1].error
    assert await count(lifecycle, Flashcard) == 0
    if empty_stage == 'quiz':
        assert lifecycle.cards.await_count == 0
        assert await count(lifecycle, QuestionBank) == 0


@pytest.mark.parametrize('failure', ['summary', 'summary_commit', 'agent_wrapper', 'agent_exception', 'agent_result'])
async def test_nonfatal_failure_is_partial_and_keeps_evidence(lifecycle, monkeypatch, failure):
    from app.core.knowledge.summary_generator import SummaryGenerator
    hook = None
    if failure == 'summary':
        monkeypatch.setattr(SummaryGenerator, 'generate', AsyncMock(side_effect=RuntimeError('simulated summary failure')))
    if failure == 'summary_commit':
        def hook(conn, cursor, statement, params, context, many):
            if statement.lower().startswith('insert into knowledge_point_nodes'):
                raise RuntimeError('simulated summary commit failure')
        event.listen(lifecycle.factory.kw['bind'].sync_engine, 'before_cursor_execute', hook)
    result = {'results': {}, 'coverage_report': None}
    if failure == 'agent_wrapper':
        result['error'] = 'simulated orchestrator wrapper failure'
    if failure == 'agent_result':
        result['results']['broken'] = AgentResult(agent='broken', status='error', error='simulated agent failure')
    method = AsyncMock(side_effect=RuntimeError('simulated agent exception')) if failure == 'agent_exception' else AsyncMock(return_value=result)
    monkeypatch.setattr(orchestrator, 'orchestrate_from_pipeline', method)
    try:
        final = (await collect(user_id='alice'))[-1]
    finally:
        if hook:
            event.remove(lifecycle.factory.kw['bind'].sync_engine, 'before_cursor_execute', hook)
    assert final.stage == PipelineStage.DONE
    assert final.result.get('status') == 'partial'
    assert final.result.get('warnings')
    assert final.result['question_count'] == 5 and final.result['card_count'] == 5
    if failure.startswith('summary'):
        assert final.result['summary_id'] is None
        assert method.await_count == 0
        assert await count(lifecycle, KnowledgeSummary) == 1  # existing fixture summary only
    elif failure != 'agent_result':
        assert final.result['agent_error']
    else:
        assert final.result['agent_results']['broken']['status'] == 'error'


@pytest.mark.parametrize('selected', [
    [PipelineStage.PARSE], [PipelineStage.KNOWLEDGE_TREE], [PipelineStage.OUTLINE],
    [PipelineStage.AGENTS], [PipelineStage.QUIZ], [PipelineStage.FLASHCARDS],
    [PipelineStage.KNOWLEDGE_TREE, PipelineStage.QUIZ],
])
async def test_partial_only_executes_requested_stages_and_finishes(lifecycle, selected):
    from app.core.knowledge import knowledge_generator
    states = [copy.deepcopy(state) async for state in pipeline.run_partial('source', selected)]
    assert lifecycle.tree.await_count == int(PipelineStage.KNOWLEDGE_TREE in selected)
    assert knowledge_generator.generate_outline.await_count == int(PipelineStage.OUTLINE in selected)
    assert lifecycle.quiz.await_count == int(PipelineStage.QUIZ in selected)
    assert lifecycle.cards.await_count == int(PipelineStage.FLASHCARDS in selected)
    for model, size in [(KnowledgeTree, 1 if PipelineStage.KNOWLEDGE_TREE in selected else 0),
                        (Outline, 1 if PipelineStage.OUTLINE in selected else 0),
                        (StudyPlan, 1 if PipelineStage.AGENTS in selected else 0),
                        (QuestionBank, 5 if PipelineStage.QUIZ in selected else 0),
                        (Flashcard, 5 if PipelineStage.FLASHCARDS in selected else 0)]:
        assert await count(lifecycle, model) == size
    assert states[-1].stage == PipelineStage.DONE
    assert states[-1].result['status'] == 'completed'
    assert states[-1].result['card_count'] == (5 if PipelineStage.FLASHCARDS in selected else 0)


@pytest.mark.parametrize('selected', [[], [PipelineStage.ERROR], [PipelineStage.DONE], ['unknown']])
async def test_invalid_partial_request_has_no_generation_side_effects(lifecycle, selected):
    with pytest.raises(ValueError):
        async for _ in pipeline.run_partial('source', selected):
            pass
    assert lifecycle.tree.await_count == 0 and lifecycle.quiz.await_count == 0 and lifecycle.cards.await_count == 0


async def test_explicit_core_user_and_model_reach_every_stage_without_parent_leak(lifecycle, monkeypatch):
    from app.core.knowledge import knowledge_generator
    from app.core.knowledge.summary_generator import SummaryGenerator
    seen = []
    original_summary = SummaryGenerator.generate
    async def tree(chunks, model, **kwargs):
        seen.append(('tree', model, get_current_user_id()))
        return {'name':'simulated tree', 'children':[]}
    async def outline(chunks, model, **kwargs):
        seen.append(('outline', model, get_current_user_id()))
        return '# simulated outline'
    async def summary(*args, **kwargs):
        seen.append(('summary', kwargs['model'], get_current_user_id()))
        return await original_summary(*args, **kwargs)
    async def quiz(text, config, model):
        seen.append(('quiz', model, get_current_user_id()))
        return [{'question_text':'simulated quiz', 'answer':'A'}] * 5
    async def cards(text, kind, number, model):
        seen.append(('cards', model, get_current_user_id()))
        return [{'front':'simulated front', 'back':'simulated back'}] * 5
    class Probe(BaseAgent):
        name = 'probe'
        async def run(self, summary_id, document_id, **kwargs):
            seen.append(('agent', kwargs.get('model'), get_current_user_id()))
            assert kwargs['user_id'] == 'alice'
            return AgentResult(agent=self.name, status='success')
    monkeypatch.setattr(knowledge_generator, 'generate_tree', tree)
    monkeypatch.setattr(knowledge_generator, 'generate_outline', outline)
    monkeypatch.setattr(SummaryGenerator, 'generate', summary)
    from app.core.quiz import quiz_generator
    from app.core.memory import card_generator
    monkeypatch.setattr(quiz_generator, 'generate', quiz)
    monkeypatch.setattr(card_generator, 'generate', cards)
    monkeypatch.setattr(AgentRegistry, 'create_all', lambda names=None:[Probe()])
    prior = get_current_user_id()
    set_current_user_id('outer-caller')
    try:
        states = await collect(user_id='alice', model='simulated-provider/model-v2')
        assert states[-1].stage == PipelineStage.DONE
        assert get_current_user_id() == 'outer-caller'
        assert len(seen) == 6
        assert all(model == 'simulated-provider/model-v2' and user == 'alice' for _, model, user in seen), seen
    finally:
        set_current_user_id(prior)


@pytest.mark.parametrize('bad', [None, {'unexpected': True}, [None]])
@pytest.mark.parametrize('stage', ['quiz', 'cards'])
async def test_invalid_provider_output_rolls_back_selected_stage(lifecycle, stage, bad):
    getattr(lifecycle, stage).return_value = bad
    selected = PipelineStage.QUIZ if stage == 'quiz' else PipelineStage.FLASHCARDS
    states = [copy.deepcopy(s) async for s in pipeline.run_partial('source', [selected], user_id='alice')]
    assert states[-1].stage == PipelineStage.ERROR
    assert await count(lifecycle, QuestionBank) == 0
    assert await count(lifecycle, Flashcard) == 0
    assert await count(lifecycle, Deck) == 0


@pytest.mark.parametrize('stage,table', [(PipelineStage.QUIZ, 'question_bank'), (PipelineStage.FLASHCARDS, 'review_schedule')])
async def test_partial_database_failure_rolls_back_and_retry_succeeds(lifecycle, stage, table):
    def fail(conn, cursor, statement, params, context, many):
        if statement.lower().startswith('insert into ' + table):
            raise RuntimeError('simulated selected stage write failure')
    engine = lifecycle.factory.kw['bind'].sync_engine
    event.listen(engine, 'before_cursor_execute', fail)
    try:
        states = [copy.deepcopy(s) async for s in pipeline.run_partial('source', [stage], user_id='alice')]
        assert states[-1].stage == PipelineStage.ERROR
        assert 'simulated selected stage write failure' in states[-1].error
    finally:
        event.remove(engine, 'before_cursor_execute', fail)
    assert await count(lifecycle, QuestionBank) == 0
    assert await count(lifecycle, Flashcard) == 0
    assert await count(lifecycle, Deck) == 0
    states = [copy.deepcopy(s) async for s in pipeline.run_partial('source', [stage], user_id='alice')]
    assert states[-1].stage == PipelineStage.DONE
    assert states[-1].result['status'] == 'completed'
    assert await count(lifecycle, QuestionBank if stage == PipelineStage.QUIZ else Flashcard) == 5


async def test_partial_forwards_options_and_does_not_call_broken_unselected_stages(lifecycle, monkeypatch):
    from app.core.knowledge import knowledge_generator
    lifecycle.tree.side_effect = RuntimeError('unselected tree must not run')
    lifecycle.cards.side_effect = RuntimeError('unselected cards must not run')
    knowledge_generator.generate_outline.side_effect = RuntimeError('unselected outline must not run')
    async def quizzes(text, config, model):
        assert config.count == 7 and config.difficulty == 'hard' and config.question_type == 'short_answer'
        assert model == 'simulated/custom-model' and get_current_user_id() == 'bob'
        return [{'question_text':'模拟问答', 'answer':'模拟答案'}] * 3
    monkeypatch.setattr('app.core.quiz.quiz_generator.generate', quizzes)
    states = [copy.deepcopy(s) async for s in pipeline.run_partial('source', [PipelineStage.QUIZ],
        model='simulated/custom-model', user_id='bob', question_count=7, difficulty='hard', question_type='short_answer')]
    assert states[-1].result['question_count'] == 3
    assert states[-1].result['card_count'] == 0
    assert lifecycle.tree.await_count == lifecycle.cards.await_count == 0
    assert await count(lifecycle, StudyPlan) == 0


@pytest.mark.parametrize('stop', ['close', 'cancel'])
async def test_partial_reclaims_cancellation_watcher_and_pending_generator(lifecycle, stop):
    started, finished, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancel = asyncio.Event()
    async def waiting(*args, **kwargs):
        started.set()
        try:
            await release.wait()
            return [{'question_text':'模拟', 'answer':'A'}]
        finally:
            finished.set()
    lifecycle.quiz.side_effect = waiting
    before = set(asyncio.all_tasks())
    iterator = pipeline.run_partial('source', [PipelineStage.QUIZ], user_id='alice', cancel_event=cancel)
    pending = None
    try:
        await anext(iterator)  # parse
        await anext(iterator)  # loaded
        await anext(iterator)  # quiz progress, no generation yet
        if stop == 'close':
            await iterator.aclose()
            assert not started.is_set()
            watchers = [task for task in asyncio.all_tasks() - before
                        if task.get_coro().__qualname__ == 'Event.wait' and not task.done()]
            assert not watchers, 'partial aclose leaked the inner cancellation watcher'
        else:
            pending = asyncio.create_task(anext(iterator))
            await asyncio.wait_for(started.wait(), 2)
            cancel.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 2)
            assert finished.is_set()
        assert await count(lifecycle, QuestionBank) == 0
        assert lifecycle.cards.await_count == 0
    finally:
        release.set()
        cancel.set()
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


@pytest.mark.parametrize('stream', [False, True])
async def test_real_http_repeat_partial_warning_failure_and_recovery(lifecycle, monkeypatch, stream):
    from app.core.knowledge.summary_generator import SummaryGenerator
    from app.api import pipeline as api
    original_summary = SummaryGenerator.generate
    samples = []
    seen = []
    async def summary(*args, **kwargs):
        seen.append((kwargs['model'], get_current_user_id()))
        return await original_summary(*args, **kwargs)
    monkeypatch.setattr(SummaryGenerator, 'generate', summary)
    url = '/api/v1/pipeline/run' + ('/stream' if stream else '')
    async def run(client, headers, model):
        response = await client.post(url, headers=headers, json={**request(), 'model':model})
        data = ([json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                if stream else response.json())
        samples.append({'http_status':response.status_code, 'body':data})
        return response, data[-1]['result'] if stream and data[-1]['stage'] == 'done' else data
    async with localhost(lifecycle.app) as client:
        response, first = await run(client, lifecycle.alice, 'simulated/model-a')
        assert response.status_code == 200 and first['status'] == 'completed'
        response, second = await run(client, lifecycle.bob, 'simulated/model-b')
        assert response.status_code == 200 and second['status'] == 'completed'
        assert first['tree_id'] != second['tree_id']
        assert seen == [('simulated/model-a', 'alice'), ('simulated/model-b', 'bob')]
        for result, owner in [(first, 'alice'), (second, 'bob')]:
            async with lifecycle.factory() as db:
                plan_id = result['agent_results']['study_plan']['result']['plan_id']
                assert (await db.get(StudyPlan, plan_id)).user_id == owner
        monkeypatch.setattr(SummaryGenerator, 'generate', AsyncMock(side_effect=RuntimeError('simulated network summary failure')))
        response, partial = await run(client, lifecycle.alice, 'simulated/model-a')
        assert response.status_code == 200 and partial['status'] == 'partial'
        assert partial['summary_id'] is None and partial['warnings'][0]['stage'] == 'summary'
        monkeypatch.setattr(SummaryGenerator, 'generate', summary)
        lifecycle.quiz.return_value = []
        response, failure = await run(client, lifecycle.alice, 'simulated/model-a')
        if stream:
            assert response.status_code == 200 and failure[-1]['stage'] == 'error'
            assert not any(e['stage'] == 'done' for e in failure)
        else:
            assert response.status_code == 500 and '没有生成有效题目' in failure['detail']
        lifecycle.quiz.return_value = [{'question_text':'模拟恢复题', 'answer':'A'}] * 5
        response, recovered = await run(client, lifecycle.alice, 'simulated/model-a')
        assert response.status_code == 200 and recovered['status'] == 'completed'
        assert recovered['warnings'] == []
        assert await count(lifecycle, Flashcard) == 20
        assert api._active_pipelines == {}
    output = Path(__file__).resolve().parents[2] / f'docs/test-round-06b4/real-http-{"sse" if stream else "rest"}.json'
    output.write_text(json.dumps({'transport':'real localhost HTTP/SSE', 'database':'disposable SQLite',
        'generation':'explicitly simulated providers', 'observed_model_users':seen, 'samples':samples}, ensure_ascii=False, indent=2), encoding='utf-8')
