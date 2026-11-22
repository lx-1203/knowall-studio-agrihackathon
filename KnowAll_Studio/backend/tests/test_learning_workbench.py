import json
from types import SimpleNamespace

import pytest

from app.core import learning_workbench as wb


def test_sections_preserve_order_tail_and_stable_identity():
    chunks = [dict(id='b', text_content='# Second\n' + 'xyz ' * 40, chunk_index=2, page_range='2'),
              SimpleNamespace(id='a', text_content='# First\nhello\n', chunk_index=1, page_range='1')]
    sections = wb.build_sections(chunks, max_chars=35)
    assert ''.join(s['text'] for s in sections) == '# First\nhello\n# Second\n' + 'xyz ' * 40
    assert all(len(s['text']) <= 35 for s in sections)
    assert sections == wb.build_sections(chunks, max_chars=35)
    assert len({s['id'] for s in sections}) == len(sections)
    assert sections[0]['title'] == 'First'
    assert sections[0]['chunk_ids'] == ['a']


def test_fingerprint_reads_actual_source():
    chunks = [dict(id='a', text_content='one', page_range='1', content_hash='unchanged')]
    first = wb.source_fingerprint(chunks)
    chunks[0]['text_content'] = 'two'
    assert first != wb.source_fingerprint(chunks)
    chunks[0]['page_range'] = '2'
    assert len(wb.source_fingerprint(chunks)) == 64


@pytest.mark.parametrize('weights,total,expected', [([1,1,1],5,[2,2,1]), ([1,9,2],2,[0,1,1]), ([0,2,1],4,[0,2,2]), ([],0,[])])
def test_budget(weights,total,expected):
    assert wb.allocate_budget(weights,total) == expected


@pytest.mark.parametrize('weights,total', [([-1],2),([float('nan')],1),([0],1),([],1),([1],-1),([1],1.5)])
def test_invalid_budget(weights,total):
    with pytest.raises(ValueError):
        wb.allocate_budget(weights,total)


def q(kind, answer, options=None):
    return dict(question='Choose correctly', question_type=kind, correct_answer=answer, options=options, analysis='Reason')


OPTIONS = [dict(label='A',text='apple'),dict(label='B',text='berry')]


@pytest.mark.parametrize('question', [q('single_choice','Z',OPTIONS),q('single_choice','AB',OPTIONS),q('multi_choice','AZ',OPTIONS),q('single_choice','A',[OPTIONS[0]]),q('single_choice','A',[OPTIONS[0],OPTIONS[0]]),q('short_answer',''), dict(question='',answer='x')])
def test_quality_rejects_invalid_questions(question):
    assert not wb.question_quality(question)['eligible']


def test_true_false_and_aliases():
    question = dict(question='Earth is round',type='true_false',answer='true')
    assert wb.question_quality(question)['eligible']
    assert wb.grade_interaction(question,'正确')['is_correct']
    assert wb.grade_interaction(q('multi_choice','AB',OPTIONS),'B,A')['is_correct']


def structured(kind='matching'):
    return q(kind,json.dumps({'A':'fruit','B':'fruit'}),[dict(label='A',text='apple',choices=['fruit','vegetable']),dict(label='B',text='berry',choices=['fruit','vegetable'])])


def test_mapping_validation_and_partial_credit():
    question = structured()
    assert wb.question_quality(question)['eligible']
    assert wb.grade_interaction(question,{'A':'fruit','B':'vegetable'})['score_ratio'] == .5
    assert wb.grade_interaction(question,{'A':'fruit','B':'fruit','forged':'fruit'})['score_ratio'] == 0
    assert wb.grade_interaction(question,'{broken')['score_ratio'] == 0
    question['correct_answer'] = '{"A":"fruit"}'
    assert not wb.question_quality(question)['eligible']


def test_ordering_permutation_and_partial_credit():
    question = q('ordering','["A","B","C"]',OPTIONS + [dict(label='C',text='cherry')])
    assert wb.question_quality(question)['eligible']
    assert wb.grade_interaction(question,['A','C','B'])['score_ratio'] == pytest.approx(1/3)
    assert wb.grade_interaction(question,['A','A','C'])['score_ratio'] == 0
    assert wb.grade_interaction(question,['A','B','C'])['is_correct']
    question['correct_answer'] = '["A","B"]'
    assert not wb.question_quality(question)['eligible']


@pytest.mark.parametrize('options', [[None, dict(label='B',text='berry')], [dict(label='a',text='apple',choices=['x','y']),dict(label='B',text='berry',choices=['x','y'])]])
def test_malformed_mapping_options_never_crash(options):
    question = q('matching','{"A":"x","B":"x"}',options)
    assert not wb.question_quality(question)['eligible']
    assert wb.grade_interaction(question,'{}')['score_ratio'] == 0


def test_partial_mapping_answers_are_allowed_but_forged_keys_are_rejected():
    assert wb.grade_interaction(structured('classification'),{'A':'fruit'})['score_ratio'] == .5


def test_chapter_continuation_merges_and_keeps_metadata():
    chunks = [dict(id='1',text_content='# 第一章\n内容\n',page_range='1',chunk_index=0),
              dict(id='2',text_content='接续内容\n',page_range='2',chunk_index=1),
              dict(id='3',text_content='# 第二章\n尾部',page_range='3',chunk_index=2)]
    sections = wb.build_sections(chunks,30)
    assert len(sections) == 2
    assert sections[0]['title'] == '第一章'
    assert sections[0]['text'] == '# 第一章\n内容\n接续内容\n'
    assert sections[0]['chunk_ids'] == ['1','2']
    assert sections[0]['page_ranges'] == ['1','2']


def test_canonical_prompt_and_unsupported_type():
    assert wb.question_quality(dict(question_text='Canonical prompt',question_type='short_answer',correct_answer='answer'))['eligible']
    quality = wb.question_quality(q('invented_type','answer'))
    assert not quality['eligible']
    assert 'unsupported_type' in quality['issues']


@pytest.mark.parametrize('kind',['cloze','calculation','formula','coding','material_analysis','term_definition'])
def test_existing_free_text_types_remain_eligible(kind):
    assert wb.question_quality(dict(question_text='Question',question_type=kind,correct_answer='answer'))['eligible']


def test_unheaded_chunks_merge_and_whitespace_pieces_are_skipped():
    sections = wb.build_sections([dict(id='1',text_content='hello',chunk_index=0),dict(id='2',text_content='world',chunk_index=1)],20)
    assert len(sections) == 1
    assert sections[0]['text'] == 'helloworld'
    assert all(s['text'].strip() for s in wb.build_sections([dict(id='1',text_content='abc'+' '*30+'xyz')],10))
