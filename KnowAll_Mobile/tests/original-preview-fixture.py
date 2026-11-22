"""Isolated HTTP fixture: production preview routes/rendering, fixture identities and document lookup.

No production database, AI or uploaded user files are touched. Run with system Python.
"""
from pathlib import Path
from types import SimpleNamespace
import sys
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'KnowAll_Studio/backend'))
import fitz
from PIL import Image
from fastapi import FastAPI, Request, HTTPException
from app.api.document_preview import router
from app.database import get_db
from app.core.auth import get_optional_user

out = root / 'KnowAll_Mobile/.tmp/round04-fixtures'
def create_fixtures():
    out.mkdir(parents=True, exist_ok=True)
    with fitz.open() as pdf:
        for title in ['细胞与能量', '遗传与复制']:
            page = pdf.new_page(width=595, height=842)
            page.insert_text((48, 68), title, fontname='china-s', fontsize=28, color=(0, .3, .25))
            page.insert_text((48, 115), '观察细胞结构，理解能量变化。', fontname='china-s', fontsize=16)
            page.insert_text((48, 170), 'E = mc', fontsize=30)
            page.insert_text((145, 154), '2', fontsize=18)
            page.draw_circle((250, 320), 95, color=(0, .5, .4), fill=(.8, 1, .9))
            page.draw_circle((250, 320), 34, color=(0, .4, .35), fill=(.2, .6, .5))
            page.insert_text((48, 510), '原件阅读保留图片、公式与中文。', fontname='china-s', fontsize=16)
        pdf.save(out / 'biology.pdf')
    Image.new('RGB', (1600, 1200), (230, 190, 70)).save(out / 'image.png')

class FixtureDatabase:
    async def get(self, model, key):
        if key not in ('bio', 'image'):
            return None
        return SimpleNamespace(id=key, file_type='pdf' if key == 'bio' else 'png',
                               local_path=str(out / ('biology.pdf' if key == 'bio' else 'image.png')),
                               metadata_={'knowledge_owner_id': 'ui-test'})

async def fixture_identity(request: Request):
    token = request.headers.get('authorization')
    if token == 'Bearer local-ui-test-token':
        return SimpleNamespace(id='ui-test')
    if token == 'Bearer other-account-token':
        return SimpleNamespace(id='other')
    raise HTTPException(401, 'Fixture requires its test bearer token')

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_db] = lambda: FixtureDatabase()
app.dependency_overrides[get_optional_user] = fixture_identity

if __name__ == '__main__':
    import uvicorn
    create_fixtures()
    uvicorn.run(app, host='127.0.0.1', port=8896, log_level='warning')
