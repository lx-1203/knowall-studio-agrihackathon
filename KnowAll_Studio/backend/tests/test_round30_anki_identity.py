"""Open compatibility candidates, actual Anki consumer; no product migration."""
import json
from types import SimpleNamespace
from app.core.anki_export import export_apkg
from tests.test_anki_consumer_export import collection,consume,card

def test_persistent_deck_rename_keeps_note_card_and_schedule(collection,tmp_path,monkeypatch):
    monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:1790000000))
    first=export_apkg([card()], '旧显示名称',str(tmp_path/'first.apkg'),deck_id='fixed-deck')
    consume(collection,first);ids=collection.find_cards('');guid=collection.get_note(collection.find_notes('')[0]).guid
    existing=collection.get_card(ids[0]);existing.queue=existing.type=2;existing.ivl=19;existing.reps=7;existing.due=43;collection.update_card(existing)
    monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:1790000001))
    result=consume(collection,export_apkg([card(back='改名后答案')], '新显示名称',str(tmp_path/'rename.apkg'),deck_id='fixed-deck'))
    assert collection.note_count()==collection.card_count()==1 and collection.find_cards('')==ids
    kept=collection.get_card(ids[0]);assert (kept.ivl,kept.reps,kept.due,kept.queue)==(19,7,43,2)
    assert collection.get_note(collection.find_notes('')[0]).guid==guid
    print('ROUND30_RENAME '+json.dumps({'native_consumer':True,'updated':len(result.log.updated),'notes':1,'card_id_schedule_kept':True}))

def test_changing_basic_to_cloze_is_a_real_consumer_conflict_not_safe_migration(collection,tmp_path,monkeypatch):
    monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:1790000000))
    consume(collection,export_apkg([card()], '类型边界',str(tmp_path/'basic.apkg'),deck_id='types'))
    guid=collection.get_note(collection.find_notes('')[0]).guid;ids=collection.find_cards('')
    monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:1790000001))
    result=consume(collection,export_apkg([card(kind='cloze',front='{{c1::新答案}}')], '类型边界',str(tmp_path/'cloze.apkg'),deck_id='types'))
    assert collection.note_count()==1 and collection.find_cards('')==ids
    assert collection.get_note(collection.find_notes('')[0]).guid==guid
    assert collection.get_note(collection.find_notes('')[0]).fields[0]=='中文问题'
    print('ROUND30_TYPE_CONFLICT '+json.dumps({'native_consumer':True,'updated':len(result.log.updated),'original_note_unchanged':True,'log':str(result.log)},ensure_ascii=False))

def test_legacy_name_without_persistent_identity_cannot_be_inferred_as_rename(collection,tmp_path,monkeypatch):
    monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:1790000000))
    legacy={'front':'旧无ID输入','back':'答案','card_type':'qa'}
    consume(collection,export_apkg([legacy], '旧名字',str(tmp_path/'first.apkg')))
    monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:1790000001))
    consume(collection,export_apkg([legacy], '新名字',str(tmp_path/'second.apkg')))
    assert collection.note_count()==collection.card_count()==2
    print('ROUND30_LEGACY_RENAME '+json.dumps({'native_consumer':True,'notes':2,'safe_rename_not_supported_without_identity':True}))
