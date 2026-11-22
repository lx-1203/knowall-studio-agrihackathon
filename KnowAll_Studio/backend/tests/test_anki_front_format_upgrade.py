"""Real format-2 producer/SQL/API/default Anki compatibility; no content edit."""
import importlib.util
from pathlib import Path
import pytest
from anki.collection import Collection, ImportAnkiPackageRequest
from app.models import Flashcard
from tests.test_anki_version_flow import boundary, package, note_rows, consume
from tests.test_flashcard_edit_flow import edits, create, persisted


@pytest.mark.parametrize('kind,front', [
    ('qa', '求 $x_1$'), ('compare', '比较 $$x_1$$'),
    ('cloze', '$x_1+{{c1::y_2::加数}}={{c2::z_3}}$'),
])
async def test_format2_front_upgrades_default_anki_without_content_or_fsrs_change(edits, boundary, tmp_path, kind, front):
    client, factory, _ = edits
    c, _ = await create(client, kind)
    async with factory() as db:
        saved = await db.get(Flashcard, c['id'])
        saved.front = front
        saved.anki_export_version = saved.content_version
        saved.anki_export_mtime = int(boundary.now)
        saved.anki_export_format = 2
        await db.commit()
    assert (await client.post('/api/v1/flashcards/review', json={'card_id':c['id'],'rating':3})).status_code == 200
    before_cloud = await persisted(factory,c['id'])
    old_source = Path(__file__).resolve().parents[2]/'docs/open-source-integration/round-28-before-anki_export.py.txt'
    old = importlib.util.module_from_spec(importlib.util.spec_from_loader('format2_producer', loader=None))
    exec(compile(old_source.read_text(encoding='utf-8'),str(old_source),'exec'),old.__dict__)
    assert old.EXPORT_FORMAT_VERSION == 2
    old_path = old.export_apkg([{**c,'front':front,'anki_export_mtime':int(boundary.now)}],
                              '纠错牌组',str(tmp_path/'format2.apkg'),deck_id=c['deck_id'])
    col = Collection(str(tmp_path/'consumer.anki2'))
    try:
        col.import_anki_package(ImportAnkiPackageRequest(package_path=old_path))
        nid = col.find_notes('')[0];guid = col.get_note(nid).guid;ids=col.find_cards('')
        assert '$' in col.get_note(nid).fields[0]
        for cid in ids:
            local = col.get_card(cid);local.queue=local.type=2
            local.ivl=17;local.reps=4;local.due=35;col.update_card(local)
        response=await package(client,c['deck_id'])
        imported=consume(col,response,tmp_path,'format3')
        assert len(imported.log.updated)==1
        assert col.get_note(nid).guid==guid and col.find_cards('')==ids
        assert (r'\(' in col.get_note(nid).fields[0] or r'\[' in col.get_note(nid).fields[0])
        for cid in ids:
            local=col.get_card(cid)
            assert (local.ivl,local.reps,local.due,local.queue)==(17,4,35,2)
        after_cloud=await persisted(factory,c['id'])
        assert after_cloud[0]['anki_export_format']==3
        assert after_cloud[0]['anki_export_mtime']==1790000001
        assert before_cloud[1:]==after_cloud[1:]
        excluded={'anki_export_mtime','anki_export_format'}
        assert {k:v for k,v in before_cloud[0].items() if k not in excluded}=={k:v for k,v in after_cloud[0].items() if k not in excluded}
        # Fresh sessions in API requests use persisted marker: no second upgrade.
        boundary.now+=10;old_sleeps=list(boundary.sleeps)
        repeated=await package(client,c['deck_id'])
        assert note_rows(response,tmp_path,'a')==note_rows(repeated,tmp_path,'b')
        assert len(consume(col,repeated,tmp_path,'unchanged').log.updated)==0
        assert boundary.sleeps==old_sleeps
    finally:
        col.close()
