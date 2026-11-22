"""Nested Markdown math must not acquire quote/list markers in real Anki."""
import pytest
from tests.test_anki_consumer_export import collection, export, consume, card


@pytest.mark.parametrize('source,expected', [
    ('> $$\n> x_1 + y_2\n> $$', r'\[x_1 + y_2\]'),
    ('- $$\n  x_1 + y_2\n  $$', r'\[x_1 + y_2\]'),
    ('> > $$\n> > x_1\n> > $$', r'\[x_1\]'),
    ('> $$\n> x_1 % note\n> + y_2\n> $$', '\\[x_1 % note\n+ y_2\\]'),
])
def test_nested_formula_has_only_its_original_tex(collection, tmp_path, source, expected):
    consume(collection, export(tmp_path, [card(back=source)]))
    note = collection.get_note(collection.find_notes('')[0])
    assert expected in note.fields[1]
    assert expected in collection.get_card(collection.find_cards('')[0]).answer()
