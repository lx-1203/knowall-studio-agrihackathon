"""A persisted old package must upgrade through real HTTP and default Anki."""
import importlib.util
from pathlib import Path

import pytest
from anki.collection import Collection, ImportAnkiPackageRequest
from app.models import Flashcard
from tests.test_anki_version_flow import boundary, package, note_rows, consume
from tests.test_flashcard_edit_flow import edits, create, persisted


async def test_old_cached_math_export_upgrades_without_content_edit(edits, boundary, tmp_path):
    client, factory, _ = edits
    c, _ = await create(client)
    async with factory() as db:
        stored = await db.get(Flashcard, c['id']); stored.back = '$x_1$'
        stored.anki_export_version = stored.content_version
        stored.anki_export_mtime = int(boundary.now)
        await db.commit()
    assert (await client.post('/api/v1/flashcards/review',json={'card_id':c['id'],'rating':3})).status_code==200
    before_cloud = await persisted(factory,c['id'])
    old_source = Path(__file__).resolve().parents[2] / 'docs/open-source-integration/round-27-before-anki_export.py.txt'
    old_module = importlib.util.module_from_spec(importlib.util.spec_from_loader('old_export', loader=None))
    exec(compile(old_source.read_text(encoding='utf-8'), str(old_source), 'exec'), old_module.__dict__)
    old_path = old_module.export_apkg([{**c, 'back':'$x_1$', 'anki_export_mtime':int(boundary.now)}],
                                      '纠错牌组', str(tmp_path/'old.apkg'), deck_id=c['deck_id'])
    col = Collection(str(tmp_path/'consumer.anki2'))
    try:
        col.import_anki_package(ImportAnkiPackageRequest(package_path=old_path))
        nid=col.find_notes('')[0]; guid=col.get_note(nid).guid; cid=col.find_cards('')[0]
        card=col.get_card(cid);card.queue=card.type=2;card.ivl=17;card.reps=4;card.due=35;col.update_card(card)
        assert '$x_1$' in col.get_note(nid).fields[1]
        result = consume(col, await package(client,c['deck_id']), tmp_path, 'format-upgraded')
        assert len(result.log.updated)==1
        assert r'\(x_1\)' in col.get_note(nid).fields[1]
        assert col.get_note(nid).guid==guid and col.find_cards('')==[cid]
        card=col.get_card(cid);assert (card.ivl,card.reps,card.due,card.queue)==(17,4,35,2)
        async with factory() as db:
            stored=await db.get(Flashcard,c['id'])
            assert stored.content_version==1 and stored.anki_export_format==3
            assert stored.anki_export_mtime>1790000000
        before=list(boundary.sleeps);boundary.now+=10
        again=await package(client,c['deck_id']);consume(col,again,tmp_path,'unchanged')
        assert col.card_count()==1 and boundary.sleeps==before
        assert note_rows(again,tmp_path,'stable')[0][1]==1790000001
        after_cloud = await persisted(factory,c['id'])
        assert before_cloud[1:] == after_cloud[1:]
        exclude={'anki_export_mtime','anki_export_format'}
        assert {k:v for k,v in before_cloud[0].items() if k not in exclude} == {k:v for k,v in after_cloud[0].items() if k not in exclude}
    finally:col.close()


async def test_format_marker_rolls_back_with_failed_encoder(edits,boundary,monkeypatch):
    client,factory,_=edits;c,_=await create(client)
    from app.api import flashcards
    def fail(*args,**kwargs):raise RuntimeError('private encoder error')
    with monkeypatch.context() as m:
        m.setattr(flashcards,'export_apkg',fail)
        assert (await client.get('/api/v1/flashcards/export/anki/'+c['deck_id'])).status_code==500
    async with factory() as db:
        saved=await db.get(Flashcard,c['id']);assert getattr(saved,'anki_export_format',None) is None
        assert saved.anki_export_mtime is None
    await package(client,c['deck_id'])
    async with factory() as db:
        saved=await db.get(Flashcard,c['id']);assert saved.anki_export_format==3
