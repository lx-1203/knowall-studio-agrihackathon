"""Actual Anki Rust consumer, isolated collections; install Anki only for tests."""
import inspect

import pytest

anki = pytest.importorskip('anki', reason='Actual Anki consumer is a test-only dependency')
from anki.collection import Collection, ImportAnkiPackageRequest, ImportAnkiPackageOptions
from app.core.anki_export import export_apkg


@pytest.fixture
def collection(tmp_path):
    col = Collection(str(tmp_path / 'consumer.anki2'))
    try:
        yield col
    finally:
        col.close()


def export(tmp_path, cards, name='中文课程', identity='deck-a', filename='export.apkg'):
    # The legacy producer has no identity input: run its real bytes for RED too.
    kwargs = {'deck_id': identity} if 'deck_id' in inspect.signature(export_apkg).parameters else {}
    return export_apkg(cards, name, str(tmp_path / filename), **kwargs)


def consume(col, path, always=False):
    options = ImportAnkiPackageOptions(update_notes=1 if always else 0)
    return col.import_anki_package(ImportAnkiPackageRequest(package_path=path, options=options))


def card(identity='card-a', kind='qa', front='中文问题', back='中文答案', tags=None):
    return {'id': identity, 'card_type': kind, 'front': front, 'back': back, 'tags': tags or []}


def test_actual_anki_accepts_package_and_displays_chinese(collection, tmp_path):
    result = consume(collection, export(tmp_path, [card()]))
    assert len(result.log.new) == 1
    assert collection.note_count() == collection.card_count() == 1
    note = collection.get_note(collection.find_notes('')[0])
    assert note.fields == ['中文问题', '<p>中文答案</p>\n']
    rendered = collection.get_card(collection.find_cards('')[0])
    assert '中文问题' in rendered.question() and '中文答案' in rendered.answer()


def test_reimport_updates_answer_without_duplicate_or_lost_scheduling(collection, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000000))
    consume(collection, export(tmp_path, [card()]))
    ids = collection.find_cards('')
    existing = collection.get_card(ids[0]); existing.queue = 2; existing.type = 2
    existing.ivl = 17; existing.reps = 4; existing.due = 35; collection.update_card(existing)
    # Same-second import behavior is verified separately below.
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000001))
    result = consume(collection, export(tmp_path, [card(back='修改后的答案')]), always=True)
    assert len(result.log.updated) == 1
    assert collection.note_count() == collection.card_count() == 1
    assert collection.find_cards('') == ids
    assert collection.get_note(collection.find_notes('')[0]).fields[1] == '<p>修改后的答案</p>\n'
    kept = collection.get_card(ids[0])
    assert (kept.ivl, kept.reps, kept.due, kept.queue) == (17, 4, 35, 2)


def test_identical_reimport_never_duplicates(collection, tmp_path):
    path = export(tmp_path, [card()]); consume(collection, path); consume(collection, path)
    assert collection.note_count() == collection.card_count() == 1


@pytest.mark.parametrize('name_b', ['不同课程', '中文课程'])
def test_distinct_decks_even_same_name_remain_distinct(collection, tmp_path, name_b):
    consume(collection, export(tmp_path, [card()], identity='deck-a'))
    consume(collection, export(tmp_path, [card(back='另一牌组答案')], name=name_b, identity='deck-b'))
    assert collection.note_count() == collection.card_count() == 2
    assert len(set(row[0] for row in collection.db.all('select did from cards'))) == 2
    assert len(set(row[0] for row in collection.db.all('select guid from notes'))) == 2


def test_two_cards_with_identical_content_keep_persistent_identity(collection, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000000))
    consume(collection, export(tmp_path, [card('a'), card('b')]))
    assert collection.note_count() == collection.card_count() == 2
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000001))
    consume(collection, export(tmp_path, [card('b'), card('a', back='新答案')]), always=True)
    assert collection.note_count() == collection.card_count() == 2
    assert sorted(row[0].split('\x1f')[1] for row in collection.db.all('select flds from notes')) == ['<p>中文答案</p>\n', '<p>新答案</p>\n']


def test_plain_text_html_and_newlines_render_safely(collection, tmp_path):
    front = 'a < b & c\n<script>alert(1)</script>'
    consume(collection, export(tmp_path, [card(front=front, back='第一行\r\n第二行')]))
    note = collection.get_note(collection.find_notes('')[0])
    assert note.fields == ['a &lt; b &amp; c<br>&lt;script&gt;alert(1)&lt;/script&gt;', '<p>第一行<br>\n第二行</p>\n']
    rendered = collection.get_card(collection.find_cards('')[0])
    assert '<script>alert(1)</script>' not in rendered.question()
    assert '&lt;script&gt;' in rendered.question()


def test_native_cloze_hides_answer_and_creates_each_blank(collection, tmp_path):
    consume(collection, export(tmp_path, [card(kind='cloze', front='{{c1::细胞核}} 与 {{c2::线粒体}}', back='完整解释')]))
    assert collection.note_count() == 1 and collection.card_count() == 2
    cards = [collection.get_card(i) for i in collection.find_cards('')]
    assert {c.ord for c in cards} == {0, 1}
    from lxml.html import fromstring
    active = next(c for c in cards if c.ord == 0)
    # Current Anki keeps the answer in a data attribute for reveal controls;
    # assert the displayed cloze text, rather than all HTML source bytes.
    assert fromstring(active.question()).xpath('//span[@class="cloze"]')[0].text_content() == '[...]'
    assert fromstring(active.answer()).xpath('//span[@class="cloze"]')[0].text_content() == '细胞核'
    assert '完整解释' in cards[0].answer()


@pytest.mark.parametrize('front', ['旧填空没有标记', '{{c0::无效标记}}', '{{c1::}}'])
def test_legacy_invalid_cloze_falls_back_to_visible_basic(collection, tmp_path, front):
    consume(collection, export(tmp_path, [card(kind='cloze', front=front)]))
    assert collection.note_count() == collection.card_count() == 1
    assert front in collection.get_card(collection.find_cards('')[0]).question()


def test_compare_preserves_escaped_table_cell_text(collection, tmp_path):
    table = '| 维度 | A | B |\n|---|---|---|\n| 大小 | <2 | >3 |'
    consume(collection, export(tmp_path, [card(kind='compare', back=table)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert '<td>&lt;2</td>' in note.fields[1] and '<td>&gt;3</td>' in note.fields[1]
    assert collection.card_count() == 1


def test_native_tags_normalize_whitespace_and_control_field_separator(collection, tmp_path):
    consume(collection, export(tmp_path, [card(front='前\x1f后', tags=['核心 重点', 'a\tb', ' 核心 重点 ', '中文'])]))
    note = collection.get_note(collection.find_notes('')[0])
    assert note.fields == ['前�后', '<p>中文答案</p>\n']
    assert set(note.tags) == {'核心_重点', 'a_b', '中文'}


def test_empty_deck_is_a_valid_import(collection, tmp_path):
    result = consume(collection, export(tmp_path, []))
    assert result.log.found_notes == collection.note_count() == collection.card_count() == 0


def test_nonexistent_output_parent_fails_without_temp_database_leak(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    with pytest.raises(OSError):
        export_apkg([card()], '课程', str(tmp_path / 'missing' / 'export.apkg'))
    assert list(tmp_path.iterdir()) == []


def test_default_output_paths_are_unique_and_ignore_display_path(tmp_path, monkeypatch):
    monkeypatch.setattr('app.core.anki_export.settings.export_dir', str(tmp_path))
    from pathlib import Path
    first = Path(export_apkg([card()], '../中文/章节'))
    second = Path(export_apkg([card()], '../中文/章节'))
    assert first != second
    assert first.parent == second.parent == tmp_path / 'anki_cards'
    assert first.is_file() and second.is_file()


def test_native_db_failure_cleans_owned_output_and_database(tmp_path, monkeypatch):
    import genanki, tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr('app.core.anki_export.settings.export_dir', str(tmp_path))
    def fail(*args, **kwargs):
        raise RuntimeError('native write failure')
    monkeypatch.setattr(genanki.Package, 'write_to_db', fail)
    with pytest.raises(RuntimeError, match='native write failure'):
        export_apkg([card()], '课程')
    assert list((tmp_path / 'anki_cards').iterdir()) == []
    assert {p.name for p in tmp_path.iterdir()} == {'anki_cards'}


def test_equal_mtime_is_skipped_even_always_and_newer_default_updates(collection, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000000))
    consume(collection, export(tmp_path, [card()]))
    updated = export(tmp_path, [card(back='同秒修改')])
    consume(collection, updated)
    assert collection.get_note(collection.find_notes('')[0]).fields[1] == '<p>中文答案</p>\n'
    consume(collection, updated, always=True)
    assert collection.note_count() == 1
    assert collection.get_note(collection.find_notes('')[0]).fields[1] == '<p>中文答案</p>\n'
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000001))
    consume(collection, export(tmp_path, [card(back='较晚修改')]))
    assert collection.note_count() == 1
    assert collection.get_note(collection.find_notes('')[0]).fields[1] == '<p>较晚修改</p>\n'


def test_legacy_three_argument_call_updates_without_persistent_ids(collection, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000000))
    note = {'front': '旧调用格式', 'back': '旧答案', 'card_type': 'qa'}
    path = str(tmp_path / 'legacy-compatible.apkg')
    export_apkg([note], '旧调用', path); consume(collection, path)
    note['back'] = '新答案'
    monkeypatch.setattr('app.core.anki_export.time', SimpleNamespace(time=lambda: 1790000001))
    export_apkg([note], '旧调用', path); consume(collection, path, always=True)
    assert collection.note_count() == collection.card_count() == 1
    assert collection.get_note(collection.find_notes('')[0]).fields[1] == '<p>新答案</p>\n'


def answer_dom(collection):
    from lxml.html import fromstring
    return fromstring(collection.get_card(collection.find_cards('')[0]).answer())


def test_markdown_compare_is_a_real_table_in_actual_anki(collection, tmp_path):
    table = '| 维度 | A | B |\n|---|---|---|\n| 大小 | <2 | >3 |'
    consume(collection, export(tmp_path, [card(kind='compare', back=table)]))
    tables = answer_dom(collection).xpath('//table')
    assert len(tables) == 1
    assert [x.text_content() for x in tables[0].xpath('.//th')] == ['维度', 'A', 'B']
    assert [x.text_content() for x in tables[0].xpath('.//td')] == ['大小', '<2', '>3']


def test_markdown_fenced_code_preserves_indent_and_escapes_html(collection, tmp_path):
    code = 'if a < 2:\n    print("<script>中文</script>")\n'
    consume(collection, export(tmp_path, [card(back='```python\n' + code + '```')]))
    dom = answer_dom(collection)
    elements = dom.xpath('//pre/code')
    assert len(elements) == 1 and elements[0].text_content() == code
    assert elements[0].get('class') == 'language-python'
    assert not dom.xpath('//script')


def test_markdown_lists_emphasis_inline_code_and_safe_link(collection, tmp_path):
    text = '1. **细胞核**\n2. `cell_id`\n\n- *线粒体*\n- [官方资料](https://example.test/learn?q=%E4%B8%AD%E6%96%87)'
    consume(collection, export(tmp_path, [card(back=text)]))
    dom = answer_dom(collection)
    assert len(dom.xpath('//ol/li')) == len(dom.xpath('//ul/li')) == 2
    assert dom.xpath('//strong')[0].text_content() == '细胞核'
    assert dom.xpath('//code')[0].text_content() == 'cell_id'
    assert dom.xpath('//em')[0].text_content() == '线粒体'
    assert dom.xpath('//a')[0].get('href') == 'https://example.test/learn?q=%E4%B8%AD%E6%96%87'


@pytest.mark.parametrize('url', ['javascript:alert%281%29', 'JaVaScRiPt:alert%281%29', 'vbscript:msgbox%281%29', 'file:///etc/passwd', 'data:text/html;base64,PHNjcmlwdD4='])
def test_markdown_native_link_validation_blocks_active_urls(collection, tmp_path, url):
    consume(collection, export(tmp_path, [card(back=f'[危险]({url})')]))
    assert not answer_dom(collection).xpath('//a')


def test_markdown_never_embeds_remote_images_or_raw_html(collection, tmp_path):
    text = '![远程图片](https://example.test/tracker.png)\n\n<img src="https://example.test/raw.png" onerror="alert(1)">\n<script>alert(1)</script>\n<iframe src="https://example.test/"></iframe>'
    consume(collection, export(tmp_path, [card(back=text)]))
    dom = answer_dom(collection)
    assert not dom.xpath('//img|//script|//iframe')
    assert '<script>alert(1)</script>' in dom.text_content()
    assert '远程图片' in dom.text_content()


def test_markdown_answer_keeps_native_cloze_and_literal_program_front(collection, tmp_path):
    consume(collection, export(tmp_path, [card(kind='cloze', front='{{c1::cell_id}} 与 **原文**', back='**完整解释**\n\n- 细胞核\n- 线粒体')]))
    dom = answer_dom(collection)
    assert dom.xpath('//span[@class="cloze"]')[0].text_content() == 'cell_id'
    assert dom.xpath('//strong')[0].text_content() == '完整解释'
    assert len(dom.xpath('//ul/li')) == 2
    assert '**原文**' in dom.text_content()


def test_concurrent_markdown_exports_keep_native_parser_isolated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from lxml.html import fromstring
    jobs = list(range(8))
    def produce(i):
        return export(tmp_path, [card(str(i), back=f'| 序号 |\n|---|\n| {i} |')], identity=f'deck-{i}', filename=f'{i}.apkg')
    with ThreadPoolExecutor(max_workers=4) as executor:
        packages = list(executor.map(produce, jobs))
    for i, package in enumerate(packages):
        col = Collection(str(tmp_path / f'consumer-{i}.anki2'))
        try:
            consume(col, package)
            dom = fromstring(col.get_card(col.find_cards('')[0]).answer())
            assert dom.xpath('//table/tbody/tr/td')[0].text_content() == str(i)
            assert col.note_count() == col.card_count() == 1
        finally:
            col.close()
