"""Front/cloze bytes consumed and rendered by real Anki, not token mocks."""
from html import escape, unescape
import pytest
from lxml.html import fromstring
from tests.test_anki_consumer_export import collection, export, consume, card


@pytest.mark.parametrize('kind', ['qa', 'compare'])
@pytest.mark.parametrize('source,expected', [
    ('求 $x_1+y_2$ 的值', r'\(x_1+y_2\)'),
    (r'**分式** $\frac{a_1}{b_2}$', r'\(\frac{a_1}{b_2}\)'),
    ('题目\n\n$$\nx_1 % comment\n+y_2\n$$', '\\[x_1 % comment\n+y_2\\]'),
    (r'前 $$x_1$$ 后', r'\[x_1\]'),
    ('> $$\n> x_1 + y_2\n> $$', r'\[x_1 + y_2\]'),
])
def test_math_front_is_native_anki_question(collection, tmp_path, kind, source, expected):
    consume(collection, export(tmp_path, [card(kind=kind, front=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert expected in note.fields[0]
    assert expected in collection.get_card(collection.find_cards('')[0]).question()
    assert collection.note_count() == collection.card_count() == 1


@pytest.mark.parametrize('source', [
    '**literal** _underscores_ [label](https://example.com)\n<script>x</script>',
    r'`$x_1$`', '```tex\n$x_1$\n$$y_2$$\n```', '    $x_1$\n    $$y_2$$',
    r'\$x\$', '价格 $5 和 $10', '$ x $', '1$x$2', '未完 $x_1',
    '{{c1::普通答案}} 和 {{c2::中文}}',
])
def test_non_math_front_remains_exact_literal(collection, tmp_path, source):
    kind = 'cloze' if source.startswith('{{c1::') else 'qa'
    consume(collection, export(tmp_path, [card(kind=kind, front=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert note.fields[0] == escape(source).replace('\n', '<br>')


@pytest.mark.parametrize('source,hidden_values', [
    ('{{c1::$x_1$::未知数}} 与 {{c2::$y_2$}}', ['x_1','y_2']),
    ('$x_1 + {{c1::y_2::加数}} = {{c2::z_3}}$', ['y_2','z_3']),
    ('$$x_1 + {{c1::y_2}} = {{c2::z_3}}$$', ['y_2','z_3']),
    # Official Anki convention: spaces separate nested TeX closing braces.
    (r'{{c1::$\frac{foo}{\frac{bar}{baz} }$}} 与 {{c2::$w_4$}}', ['foo','w_4']),
])
def test_math_cloze_native_hiding_and_card_ordinals(collection, tmp_path, source, hidden_values):
    consume(collection, export(tmp_path, [card(kind='cloze', front=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert r'\(' in note.fields[0] or r'\[' in note.fields[0]
    assert collection.note_count() == 1 and collection.card_count() == 2
    cards = sorted((collection.get_card(cid) for cid in collection.find_cards('')), key=lambda c:c.ord)
    assert [c.ord for c in cards] == [0, 1]
    for c in cards:
        question, answer = c.question(), c.answer()
        assert 'cloze' in question and 'cloze' in answer
        assert '{{c' not in question and '{{c' not in answer
        assert (r'\(' in question or r'\[' in question)
        assert (r'\(' in answer or r'\[' in answer)
        hidden = hidden_values[c.ord]
        visible_question = fromstring(question).text_content()
        assert hidden not in visible_question
        assert hidden in fromstring(answer).text_content()


@pytest.mark.parametrize('command', [r'\href{javascript:alert(1)}{bad}', r'\require{html}', r'\def\a{evil}', r'\htmlClass{bad}{x}'])
def test_front_unsupported_math_stays_visible_code(collection, tmp_path, command):
    source = '$'+command+'$ 与 $x_1$\n\n<script>bad</script> ![img](https://example.com/x) [bad](javascript:alert(1))'
    consume(collection, export(tmp_path, [card(front=source)]))
    html = collection.get_note(collection.find_notes('')[0]).fields[0]
    dom = fromstring(html)
    assert not dom.xpath('//script|//img|//a[starts-with(@href,"javascript:")]')
    assert any(command in node.text_content() for node in dom.xpath('//code'))
    assert r'\(x_1\)' in unescape(html)
