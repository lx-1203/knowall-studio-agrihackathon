"""The same visibility check as concept detail guards source searches."""
from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.knowledge_network import concept_context
from app.core.auth import get_current_user
from app.core.knowledge_authority import schedule_source_searches, source_search_response
from app.database import get_db

router = APIRouter(prefix='/api/v1/knowledge-network', tags=['knowledge-network'])


@router.post('/concepts/{concept_id}/sources/search')
async def search_sources(concept_id: str, background: BackgroundTasks, db=Depends(get_db), user=Depends(get_current_user)):
    await concept_context(db, concept_id, user.id)
    result = await source_search_response(db, concept_id)
    if result['status'] == 'searching':
        background.add_task(schedule_source_searches, [concept_id], user.id, manual=True)
    return result
