"""Real producer bytes consumed by Anki; no renderer mocks."""
from html import unescape
from types import SimpleNamespace

import pytest

from tests.test_anki_consumer_export import collection, export, consume, card


@pytest.mark.parametrize('kind,front', [
    ('qa', '公式 $x_1$'), ('compare', '比较公式'),
    ('cloze', '{{c1::面积}}和{{c2::周长}}'),
])
def test_actual_math_back_preserves_front_type_and_identity(collection, tmp_path, kind, front):
    source = r'面积 $a_1^2 + b_2^2$。' + '\n\n' + r'$$\frac{1}{2} < x & y$$'
    consume(collection, export(tmp_path, [card(kind=kind, front=front, back=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    if kind == 'qa':
        assert r'\(x_1\)' in note.fields[0]
        assert '公式' in note.fields[0]
    else:
        assert note.fields[0] == front
    assert r'\(a_1^2 + b_2^2\)' in note.fields[1]
    assert r'\[\frac{1}{2} &lt; x &amp; y\]' in note.fields[1]
    assert collection.card_count() == (2 if kind == 'cloze' else 1)
    assert r'\(a_1^2 + b_2^2\)' in collection.get_card(collection.find_cards('')[0]).answer()


@pytest.mark.parametrize('source,expected', [
    (r'$\frac{a_1}{b_2}$', r'\(\frac{a_1}{b_2}\)'),
    (r'前 $$x_1^2$$ 后', r'\[x_1^2\]'),
    ('$$\nx_1 % keep comment\n+ y_2\n$$', '\\[x_1 % keep comment\n+ y_2\\]'),
    ('| A | B |\n|---|---|\n| $x_1$ | $y_2$ |', r'\(y_2\)'),
    (r'**结论** $x_1$ 与 $y_2$', r'\(y_2\)'),
])
def test_tex_survives_actual_anki_field_rendering(collection, tmp_path, source, expected):
    consume(collection, export(tmp_path, [card(back=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert expected in note.fields[1]
    assert expected in collection.get_card(collection.find_cards('')[0]).answer()


@pytest.mark.parametrize('source,visible', [
    (r'`$x_1$`', '$x_1$'),
    ('```tex\n$x_1$\n$$y_2$$\n```', '$x_1$'),
    (r'\$x\$', '$x$'),
    ('价格 $5 和 $10', '价格 $5 和 $10'),
    ('剩余 $x', '剩余 $x'),
    ('尾部 $$', '尾部 $$'),
    ('$ x $', '$ x $'),
    ('1$x$2', '1$x$2'),
])
def test_code_currency_escape_and_unfinished_input_remain_literal(collection, tmp_path, source, visible):
    from lxml.html import fromstring
    consume(collection, export(tmp_path, [card(back=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert visible in fromstring(note.fields[1]).text_content()
    assert r'\(' not in note.fields[1] and r'\[' not in note.fields[1]


def test_formula_html_is_escaped_and_markdown_security_retained(collection, tmp_path):
    from lxml.html import fromstring
    source = r'$<img src=x onerror=alert(1)> + <script>bad</script>$' + '\n\n' + '[bad](javascript:alert(1)) ![pic](https://example.com/x.png)'
    consume(collection, export(tmp_path, [card(back=source)]))
    html = collection.get_note(collection.find_notes('')[0]).fields[1]
    dom = fromstring(html)
    assert not dom.xpath('//script|//img|//a[starts-with(@href,"javascript:")]')
    assert r'\(<img src=x onerror=alert(1)> + <script>bad</script>\)' in unescape(html)


def test_default_math_reimport_keeps_real_anki_progress(collection, tmp_path, monkeypatch):
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000000))
    consume(collection, export(tmp_path, [card(back=r'$x_1$')]))
    nid = collection.find_notes('')[0]; note = collection.get_note(nid)
    cid = collection.find_cards('')[0]; c = collection.get_card(cid)
    c.queue = c.type = 2; c.ivl = 17; c.reps = 4; c.due = 35; collection.update_card(c)
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000001))
    result = consume(collection, export(tmp_path, [card(back=r'$y_2$')]))
    assert len(result.log.updated) == 1
    assert collection.find_notes('') == [nid] and collection.find_cards('') == [cid]
    current = collection.get_note(nid); c = collection.get_card(cid)
    assert current.guid == note.guid and r'\(y_2\)' in current.fields[1]
    assert (c.ivl, c.reps, c.due, c.queue) == (17, 4, 35, 2)


@pytest.mark.parametrize('tex', [
    r'\href{javascript:alert(1)}{bad}', r'\require{html}\htmlClass{bad}{x}',
    r'\def\f{\href}\f{javascript:alert(1)}{bad}',
    r'\csname href\endcsname{javascript:alert(1)}{bad}',
    r'\includegraphics{https://example.com/x}', r'\unknown{x}',
])
def test_extensions_and_unknown_commands_are_visible_code_not_active_tex(collection, tmp_path, tex):
    from lxml.html import fromstring
    consume(collection, export(tmp_path, [card(back='$' + tex + '$')]))
    html = collection.get_note(collection.find_notes('')[0]).fields[1]
    codes = fromstring(html).xpath('//code')
    assert len(codes) == 1 and tex in codes[0].text_content()
    assert r'\(' not in html and r'\[' not in html
