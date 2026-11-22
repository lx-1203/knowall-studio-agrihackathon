"""Text application and character-token oracle independent of OT coordinate formulas."""
from copy import deepcopy
import itertools

import pytest
from app.websocket.sync_server import _ot_transform


def insert(pos, value='XY', path=None):
    return {'operation': 'insert', 'position': pos, 'value': value, 'path': ['body'] if path is None else path}


def delete(pos, length, path=None):
    return {'operation': 'delete', 'position': pos, 'length': length, 'path': ['body'] if path is None else path}


def apply(text, op):
    p = op['position']
    assert 0 <= p <= len(text), (text, op)
    if op['operation'] == 'insert':
        return text[:p] + op['value'] + text[p:]
    assert 0 <= op['length'] <= len(text)-p, (text, op)
    return text[:p] + text[p+op['length']:]


def expected(text, a, b):
    """Both deletes remove original characters; strictly interior insertion is deleted.

    Insert events attach to gaps in the original text; the committed b precedes a
    when they attach to the same gap. No transformed coordinates are used here.
    """
    deleted = set()
    deletes = [op for op in (a, b) if op['operation'] == 'delete']
    for op in deletes:
        deleted.update(range(op['position'], op['position']+op['length']))
    result = []
    for gap in range(len(text)+1):
        for op in (b, a):
            if op['operation'] == 'insert' and op['position'] == gap:
                if not any(d['position'] < gap < d['position']+d['length'] for d in deletes):
                    result.append(op['value'])
        if gap < len(text) and gap not in deleted:
            result.append(text[gap])
    return ''.join(result)


CASES = [
    ('partial-before', delete(0, 4), delete(2, 3), 'f'),
    ('partial-after', delete(2, 3), delete(0, 4), 'f'),
    ('same-start-longer', delete(0, 5), delete(0, 2), 'f'),
    ('same-start-shorter', delete(0, 2), delete(0, 5), 'f'),
    ('contained', delete(2, 2), delete(1, 4), 'af'),
    ('insert-interior', insert(3), delete(1, 4), 'af'),
    ('delete-interior', delete(1, 4), insert(3), 'af'),
    ('insert-end', insert(5), delete(1, 4), 'aXYf'),
    ('insert-start', insert(1), delete(1, 4), 'aXYf'),
    ('insert-tie', insert(3), insert(3, 'Z'), 'abcZXYdef'),
    ('zero-delete', insert(2), delete(2, 0), 'abXYcdef'),
]


@pytest.mark.parametrize('name,a,b,want', CASES, ids=[x[0] for x in CASES])
def test_explicit_text_cases(name, a, b, want):
    original = deepcopy((a, b))
    anew, bnew = _ot_transform(a, b)
    assert apply(apply('abcdef', b), anew) == want
    assert apply(apply('abcdef', a), bnew) == want
    assert (a, b) == original


@pytest.mark.parametrize('pa,pb', [(['title'], ['body']), (['a.b'], ['a','b']), ([0], ['0'])])
@pytest.mark.parametrize('kind', ['insert', 'delete'])
def test_independent_paths_keep_operations(pa, pb, kind):
    a = insert(3, path=pa) if kind == 'insert' else delete(1, 4, path=pa)
    b = insert(0, path=pb) if kind == 'insert' else delete(0, 3, path=pb)
    assert _ot_transform(a, b) == (a, b)


@pytest.mark.parametrize('types', ['ii', 'id', 'di', 'dd'])
@pytest.mark.parametrize('text', ['abcdef', '甲乙丙丁戊己'])
def test_finite_domain_converges_to_character_oracle(types, text, record_property):
    inserts = [insert(p) for p in range(len(text)+1)]
    deletes = [delete(p, n) for p in range(len(text)+1) for n in range(len(text)-p+1)]
    kinds = {'i':inserts, 'd':deletes}
    pairs = 0
    for a, b in itertools.product(kinds[types[0]], kinds[types[1]]):
        before = deepcopy((a,b))
        anew, bnew = _ot_transform(a, b)
        want = expected(text, a, b)
        assert apply(apply(text, b), anew) == want, (text, a, b, anew, bnew)
        assert apply(apply(text, a), bnew) == want, (text, a, b, anew, bnew)
        assert (a,b) == before
        pairs += 1
    record_property('oracle_pairs', pairs)


def test_missing_and_empty_paths_preserve_legacy_same_text_semantics():
    a, b = insert(1), insert(0)
    a.pop('path')
    b['path'] = []
    anew, _ = _ot_transform(a,b)
    assert anew['position'] == 3


def test_non_text_operation_retains_existing_passthrough():
    a = {'operation':'set_attribute','path':['body'], 'value':True}
    b = insert(0)
    assert _ot_transform(a,b) == (a,b)
