"""Flashcard and review API routes"""
from datetime import datetime, timezone
import json
import uuid
import os
import tempfile
import asyncio
import sqlite3
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError, OperationalError, TimeoutError as PoolTimeoutError
from pydantic import BaseModel, Field, field_validator
from app.database import get_db
from app.core.utc_time import utc_iso as _deck_created_at
from app.models import Flashcard, Deck, ReviewSchedule, ReviewLog
from app.core.memory import card_generator, fsrs
from app.core.anki_export import export_apkg
from app.core.anki_versions import prepare_anki_export, anki_export_session, commit_anki_export
from app.core.model_connections import choose_request_model
from app.core.auth import get_optional_user, get_user_id, load_user_api_keys

from app.core.card_access import visible_deck, visible_cards, viewer_id, require_card, filter_card_results

class _AnkiExportRoute(APIRoute):
    """Include native authentication dependencies in export's pool boundary."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except PoolTimeoutError as error:
                raise HTTPException(503, '导出服务暂时繁忙，请稍后重试',
                                    headers={'Retry-After': '1'}) from error
        return handle


router = APIRouter(prefix="/api/v1/flashcards", tags=["flashcards"])



class CreateDeckRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)

    @field_validator('name')
    @classmethod
    def nonempty_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('牌组名称不能为空')
        return value


@router.post('/decks', status_code=201)
async def create_deck(req: CreateDeckRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    try:
        deck = Deck(name=req.name, description=req.description, user_id=viewer_id(current_user))
        db.add(deck)
        await db.flush()
        await db.commit()
        return {'id': deck.id, 'name': deck.name, 'card_count': deck.card_count,
                'created_at': _deck_created_at(deck.created_at)}
    except Exception as error:
        await db.rollback()
        raise HTTPException(500, 'Unable to create deck') from error


class GenerateCardsRequest(BaseModel):
    knowledge_text: str  # or chunk_ids
    card_type: Literal['qa', 'cloze', 'compare'] = "qa"
    count: int = Field(default=20, ge=1, le=100, strict=True)
    deck_name: str = Field(default="默认牌组", max_length=200)
    model: str = "deepseek-chat"

    @field_validator('knowledge_text', 'deck_name', 'model')
    @classmethod
    def nonempty_text(cls, value):
        if not value.strip():
            raise ValueError('内容不能为空')
        return value


class ReviewRequest(BaseModel):
    card_id: str
    rating: int = Field(ge=1, le=4)  # 1-4: Again/Hard/Good/Easy


class CreateCardRequest(BaseModel):
    deck_id: str = Field(min_length=1, max_length=36)
    card_type: Literal['qa', 'cloze', 'compare'] = 'qa'
    front: str = Field(min_length=1, max_length=20000)
    back: str = Field(min_length=1, max_length=20000)
    hints: str = Field(default='', max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class CardContentSnapshot(BaseModel):
    card_type: Literal['qa', 'cloze', 'compare']
    front: str = Field(max_length=20000)
    back: str = Field(max_length=20000)
    hints: str = Field(default='', max_length=1000)


class EditCardRequest(BaseModel):
    front: str = Field(min_length=1, max_length=20000)
    back: str = Field(min_length=1, max_length=20000)
    hints: str = Field(default='', max_length=1000)
    original: CardContentSnapshot


def _card_content(card):
    return {'id': card.id, 'card_type': card.card_type, 'front': card.front,
            'back': card.back, 'hints': card.hints or '', 'tags': card.tags,
            'deck_id': card.deck_id}


@router.get('/cards/{card_id}')
async def get_card(card_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    return _card_content(await require_card(db, card_id, viewer_id(current_user)))


@router.patch('/cards/{card_id}')
async def edit_card(card_id: str, req: EditCardRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    user_id = viewer_id(current_user)
    card = await require_card(db, card_id, user_id)
    values = req.model_dump(exclude={'original'})
    valid, _ = card_generator.validate_card(values, card.card_type)
    if not valid:
        raise HTTPException(422, 'Invalid card content')
    changed = any(values[key] != _card_content(card)[key] for key in values)
    try:
        # Native conditional SQL also guards a second worker's accepted edit.
        # Only content is written; identity, provenance and FSRS remain intact.
        result = await db.execute(update(Flashcard).where(
            Flashcard.id == card_id, visible_cards(user_id),
            Flashcard.content_version == card.content_version,
            Flashcard.card_type == req.original.card_type,
            Flashcard.front == req.original.front,
            Flashcard.back == req.original.back,
            func.coalesce(Flashcard.hints, '') == req.original.hints,
        ).values(**values, content_version=Flashcard.content_version + int(changed)).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, 'Card content changed; reload before editing')
        await db.commit()
        return {**_card_content(card), **values}
    except HTTPException:
        raise
    except Exception as error:
        await db.rollback()
        raise HTTPException(500, 'Unable to update card') from error


@router.post('/cards', status_code=201)
async def create_card(req: CreateCardRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    deck = (await db.execute(select(Deck).where(
        Deck.id == req.deck_id, visible_deck(Deck.id, viewer_id(current_user))
    ))).scalar_one_or_none()
    if deck is None:
        raise HTTPException(404, 'Deck not found')
    valid, _ = card_generator.validate_card(req.model_dump(), req.card_type)
    if not valid:
        raise HTTPException(422, 'Invalid card content')
    try:
        card = Flashcard(**req.model_dump())
        db.add(card)
        await db.flush()
        state = fsrs.init_card()
        db.add(ReviewSchedule(card_id=card.id, state=state['state'], fsrs_version=state['fsrs_version'],
            fsrs_stability=state['stability'], fsrs_difficulty=state['difficulty'],
            fsrs_retrievability=state['retrievability'], review_count=state['review_count']))
        await db.execute(update(Deck).where(Deck.id == deck.id).values(
            card_count=func.coalesce(Deck.card_count, 0) + 1
        ).execution_options(synchronize_session=False))
        await db.commit()
        return {'id': card.id, **req.model_dump()}
    except Exception as error:
        await db.rollback()
        raise HTTPException(500, 'Unable to save card') from error


@router.post("/generate")
async def generate_cards(
    req: GenerateCardsRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_optional_user),
):
    """Generate flashcards from knowledge text via API."""
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, get_user_id(current_user), db)

    try:
        cards = await card_generator.generate(req.knowledge_text, req.card_type, req.count, req.model)
        if not isinstance(cards, list) or not cards:
            raise ValueError('没有生成有效闪卡')
        normalized = []
        for c in cards:
            if not isinstance(c, dict) or any(not isinstance(c.get(k), str) for k in ('front', 'back')):
                raise ValueError('闪卡内容格式无效')
            card_type = c.get('card_type') or req.card_type
            if card_type not in ('qa', 'cloze', 'compare'):
                raise ValueError('闪卡类型无效')
            valid, reason = card_generator.validate_card(c, card_type)
            if not valid:
                raise ValueError(f'闪卡内容无效: {reason}')
            tags, hints = c.get('tags'), c.get('hints')
            tags = [] if tags is None else tags
            hints = '' if hints is None else hints
            # Prompt templates also return structured domain/memory tag lists.
            tag_lists = tags.values() if isinstance(tags, dict) else [tags]
            if not isinstance(tags, (list, dict)) or any(
                not isinstance(values, list) or any(not isinstance(t, str) for t in values)
                for values in tag_lists
            ) or not isinstance(hints, str):
                raise ValueError('闪卡提示或标签格式无效')
            normalized.append({**c, 'card_type': card_type, 'tags': tags, 'hints': hints})

        # All provider calls/validation finish before any business write.
        # Use the same first-deck identity as FlashcardAgent; retain old IDs.
        deck_stmt = select(Deck).where(
            Deck.name == req.deck_name, visible_deck(Deck.id, viewer_id(current_user))
        ).order_by(Deck.created_at, Deck.id).limit(1)
        deck = (await db.execute(deck_stmt)).scalar_one_or_none()
        if deck is None:
            deck = Deck(id=str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
                ['knowall:agent-deck', viewer_id(current_user), req.deck_name], ensure_ascii=False
            ))), name=req.deck_name, description='')
            db.add(deck)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                deck = (await db.execute(deck_stmt)).scalar_one_or_none()
                if deck is None:
                    raise

        saved_cards = []
        for c in normalized[:req.count]:
            card = Flashcard(card_type=c['card_type'], front=c['front'], back=c['back'],
                             hints=c['hints'], tags=c['tags'], deck_id=deck.id)
            db.add(card)
            await db.flush()
            schedule_state = fsrs.init_card()
            db.add(ReviewSchedule(card_id=card.id,
                fsrs_stability=schedule_state['stability'], fsrs_difficulty=schedule_state['difficulty'],
                fsrs_retrievability=schedule_state['retrievability'], state=schedule_state['state']))
            saved_cards.append({'id':card.id, 'card_type':card.card_type,
                'front':card.front, 'back':card.back, 'hints':card.hints})
        await db.execute(update(Deck).where(Deck.id == deck.id).values(
            card_count=func.coalesce(Deck.card_count, 0) + len(saved_cards)
        ).execution_options(synchronize_session=False))
        await db.commit()
        return {'generated_count':len(saved_cards), 'cards':saved_cards, 'deck_id':deck.id}
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Generation failed: {str(e)}") from e


@router.post("/review")
async def review_card(req: ReviewRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Submit a review rating for a flashcard. Returns updated schedule."""
    from sqlalchemy import select

    result = await db.execute(
        select(ReviewSchedule).where(ReviewSchedule.card_id == req.card_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(404, "Card schedule not found")

    await require_card(db, req.card_id, viewer_id(current_user))

    # Build state dict
    state = {
        "stability": schedule.fsrs_stability or 0,
        "difficulty": schedule.fsrs_difficulty or 0,
        "retrievability": schedule.fsrs_retrievability or 0,
        "next_review_at": schedule.next_review_at,
        "last_review_at": schedule.last_review_at,
        "review_count": schedule.review_count,
        "state": schedule.state,
        "fsrs_version": schedule.fsrs_version,
    }

    # Process with FSRS
    updated = fsrs.review(state, req.rating)

    # Update DB
    schedule.fsrs_stability = updated["stability"]
    schedule.fsrs_difficulty = updated["difficulty"]
    schedule.fsrs_retrievability = updated["retrievability"]
    schedule.next_review_at = updated["next_review_at"]
    schedule.last_review_at = updated["last_review_at"]
    schedule.review_count = updated["review_count"]
    schedule.state = updated["state"]
    schedule.fsrs_version = updated["fsrs_version"]

    # Log review
    log = ReviewLog(card_id=req.card_id, rating=req.rating)
    db.add(log)
    await db.commit()

    return {
        "card_id": req.card_id,
        "next_review_at": schedule.next_review_at.isoformat() if schedule.next_review_at else None,
        "state": schedule.state,
        "stability": round(schedule.fsrs_stability or 0, 2),
        "review_count": schedule.review_count,
    }


@router.get("/due")
async def get_due_cards(limit: int = 20, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user), deck_id: str | None = None):
    """Get cards due for review today (SQL-level filtering for performance)."""
    from sqlalchemy import select, and_, or_

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # SQL-level filtering: new cards OR cards past their review time
    result = await db.execute(
        select(Flashcard, ReviewSchedule)
        .join(ReviewSchedule, Flashcard.id == ReviewSchedule.card_id)
        .where(
            or_(
                ReviewSchedule.state == "new",
                and_(
                    ReviewSchedule.next_review_at.isnot(None),
                    ReviewSchedule.next_review_at <= now,
                ),
            )
        )
        .where(visible_cards(viewer_id(current_user)))
        .where(Flashcard.deck_id == deck_id if deck_id else True)
        .order_by(ReviewSchedule.next_review_at.isnot(None), ReviewSchedule.next_review_at.asc(), Flashcard.id)
        .limit(limit)
    )
    rows = result.all()

    due_cards = []
    for card, schedule in rows:
        due_cards.append({
            "id": card.id,
            "card_type": card.card_type,
            "front": card.front,
            "back": card.back,
            "hints": card.hints,
            "tags": card.tags,
            "schedule": {
                "state": schedule.state,
                "stability": schedule.fsrs_stability,
                "difficulty": schedule.fsrs_difficulty,
                "next_review_at": schedule.next_review_at.isoformat() if schedule.next_review_at else None,
                "review_count": schedule.review_count,
            },
        })

    # Load balancing: if all cards are due within the same hour, spread them
    if len(due_cards) > 50:
        from app.core.memory import fsrs
        # Only reorder cards, don't replace the list with balance_load output
        balanced = fsrs.balance_load(
            [{"schedule": c["schedule"], "_idx": i} for i, c in enumerate(due_cards)],
            max_per_day=50,
        )
        # Reorder due_cards based on balanced output
        if isinstance(balanced, list) and len(balanced) == len(due_cards):
            reordered = []
            seen = set()
            for item in balanced:
                idx = item.get("_idx", 0) if isinstance(item, dict) else 0
                if idx not in seen and 0 <= idx < len(due_cards):
                    reordered.append(due_cards[idx])
                    seen.add(idx)
            # Append any cards not covered by balanced output
            for i, card in enumerate(due_cards):
                if i not in seen:
                    reordered.append(card)
            due_cards = reordered

    return {
        "due_count": len(due_cards),
        "cards": due_cards,
    }


@router.get("/decks")
async def list_decks(
    limit: int = 1000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """List accessible flashcard decks."""
    result = await db.execute(
        select(Deck).where(visible_deck(Deck.id, viewer_id(current_user))).order_by(Deck.created_at.desc()).offset(offset).limit(limit)
    )
    decks = result.scalars().all()
    return [
        {"id": d.id, "name": d.name, "card_count": d.card_count, "created_at": _deck_created_at(d.created_at)}
        for d in decks
    ]


async def export_deck_anki(deck_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Export a deck as Anki .apkg file."""
    result = await db.execute(select(Deck).where(Deck.id == deck_id, visible_deck(Deck.id, viewer_id(current_user))))
    deck = result.scalar_one_or_none()
    if not deck:
        raise HTTPException(404, "Deck not found")

    # Rollback while waiting expires ORM entities; retain trusted display ID.
    deck_name, identity, export_user_id = deck.name, deck.id, viewer_id(current_user)

    output_path = None
    try:
        async with anki_export_session(db) as export_db:
            card_dicts = await prepare_anki_export(export_db, identity, export_user_id)
            # Keep user display names out of disk paths; each response owns its file.
            descriptor, output_path = tempfile.mkstemp(suffix='.apkg')
            os.close(descriptor)
            await run_in_threadpool(export_apkg, card_dicts, deck_name,
                                    output_path=output_path, deck_id=identity)
            await commit_anki_export(export_db)
        return FileResponse(
            output_path,
            media_type="application/zip",
            filename=f"{deck_name}.apkg",
            background=BackgroundTask(Path(output_path).unlink, missing_ok=True),
        )
    except BaseException as error:
        await db.rollback()
        if output_path:
            Path(output_path).unlink(missing_ok=True)
        if isinstance(error, (HTTPException, asyncio.CancelledError, PoolTimeoutError)):
            raise
        if not isinstance(error, Exception):
            raise
        if (isinstance(error, OperationalError) and
                (getattr(error.orig, 'sqlite_errorcode', None) or 0) & 255 == sqlite3.SQLITE_BUSY):
            raise HTTPException(503, '导出暂时繁忙，请稍后重试',
                                headers={'Retry-After': '1'}) from error
        raise HTTPException(500, "Unable to export deck") from error


# Public FastAPI override covers dependency resolution without changing the
# normal authentication dependency, its cache or any other flashcard route.
router.add_api_route('/export/anki/{deck_id}', export_deck_anki, methods=['GET'],
                     route_class_override=_AnkiExportRoute)


# ── Search & Related Cards ──────────────────────────────────────


@router.get("/search")
async def search_cards(
    q: str = "",
    top_k: int = 10,
    tags: str = "",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Semantic/keyword search for flashcards.

    Falls back to SQL LIKE search when ChromaDB is unavailable.
    """
    from sqlalchemy import or_

    if not q.strip():
        return {"results": [], "search_method": "empty_query"}

    # Try ChromaDB semantic search first
    try:
        from app.core.memory.retrieval import card_retriever
        results = await card_retriever.search(q, top_k)
        results = await filter_card_results(db, results, viewer_id(current_user))
        if results:
            # Vector metadata may predate a content edit. Keep relevance scores,
            # but always display authoritative SQL content and ownership.
            rows = (await db.execute(select(Flashcard).where(
                Flashcard.id.in_([item['id'] for item in results]),
                visible_cards(viewer_id(current_user)),
            ))).scalars().all()
            current = {card.id: _card_content(card) for card in rows}
            return {"results": [{**item, **current[item['id']]} for item in results
                                if item['id'] in current], "search_method": "semantic"}
    except Exception:
        pass

    # Fallback: SQL LIKE keyword search
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    conditions = [
        or_(
            Flashcard.front.ilike(f"%{q}%"),
            Flashcard.back.ilike(f"%{q}%"),
            Flashcard.hints.ilike(f"%{q}%"),
        )
    ]

    stmt = select(Flashcard).where(*conditions, visible_cards(viewer_id(current_user))).limit(top_k)
    result = await db.execute(stmt)
    cards = result.scalars().all()

    return {
        "results": [
            {
                "id": c.id,
                "card_type": c.card_type,
                "front": c.front,
                "back": c.back,
                "hints": c.hints,
                "tags": c.tags,
                "deck_id": c.deck_id,
            }
            for c in cards
        ],
        "search_method": "keyword_fallback",
    }


@router.get("/related/{card_id}")
async def get_related_cards(
    card_id: str,
    top_k: int = 5,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Get cards related to the given card (same knowledge point or deck)."""
    from app.core.memory.feedback_loop import feedback_engine

    await require_card(db, card_id, viewer_id(current_user))
    related = await feedback_engine.get_related_cards(card_id, top_k, user_id=viewer_id(current_user))
    return {"card_id": card_id, "related_count": len(related), "cards": related}


# ── Deck Summary ─────────────────────────────────────────────────


class DeckSummaryRequest(BaseModel):
    model: str = "deepseek-chat"


@router.post("/deck/{deck_id}/summary")
async def generate_deck_summary(
    deck_id: str,
    req: DeckSummaryRequest = DeckSummaryRequest(),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Generate an AI summary of a flashcard deck's content."""
    from app.prompts import prompt_engine
    from app.core.api_scheduler import api_client, TaskType, GenerationConfig

    # Load deck
    result = await db.execute(select(Deck).where(Deck.id == deck_id, visible_deck(Deck.id, viewer_id(current_user))))
    deck = result.scalar_one_or_none()
    if not deck:
        raise HTTPException(404, "Deck not found")

    # Load cards
    result = await db.execute(
        select(Flashcard).where(Flashcard.deck_id == deck_id).limit(50)
    )
    cards = result.scalars().all()

    if not cards:
        raise HTTPException(404, "No cards in deck")

    # Build type distribution
    type_dist = {}
    for c in cards:
        type_dist[c.card_type] = type_dist.get(c.card_type, 0) + 1
    type_dist_str = ", ".join(f"{k}: {v}张" for k, v in type_dist.items())

    # Sample cards for context
    sample = "\n\n".join(
        f"[{c.card_type}] Q: {c.front[:100]}\nA: {c.back[:100]}"
        for c in cards[:10]
    )

    messages = prompt_engine.render(
        "flashcard", "deck_summary",
        deck_name=deck.name,
        card_count=str(len(cards)),
        type_distribution=type_dist_str,
        sample_cards=sample,
    )

    result = await api_client.generate(
        task_type=TaskType.FLASHCARD_GEN,
        messages=messages,
        prompt_template_id="flashcard.deck_summary",
        generation_content=deck.name + str(len(cards)),
        config=GenerationConfig(model=req.model, max_tokens=1024, temperature=0.5),
    )

    import json as json_module
    try:
        summary = json_module.loads(result.content)
    except json_module.JSONDecodeError:
        summary = {"summary": result.content[:300]}

    return {
        "deck_id": deck_id,
        "deck_name": deck.name,
        "card_count": len(cards),
        "summary": summary.get("summary", ""),
        "core_topics": summary.get("core_topics", []),
        "difficulty_level": summary.get("difficulty_level", "medium"),
        "estimated_study_time_minutes": summary.get("estimated_study_time_minutes", 30),
        "learning_tips": summary.get("learning_tips", ""),
    }
