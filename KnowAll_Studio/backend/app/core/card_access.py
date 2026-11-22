"""Keep owned and learning-pack decks private; retain legacy shared cards."""
from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased
from app.models import Deck, Flashcard, StudyPack


def viewer_id(user):
    return getattr(user, 'id', None) or 'local_user'


def visible_deck(deck_column, user_id):
    owner = aliased(Deck)
    return (~exists(select(StudyPack.id).where(
        StudyPack.deck_id == deck_column, StudyPack.user_id != user_id))
        & ~exists(select(owner.id).where(
            owner.id == deck_column, owner.user_id.is_not(None), owner.user_id != user_id)))


def visible_cards(user_id):
    return visible_deck(Flashcard.deck_id, user_id)


async def require_card(db, card_id, user_id):
    card = (await db.execute(select(Flashcard).where(
        Flashcard.id == card_id, visible_cards(user_id)))).scalar_one_or_none()
    if card is None:
        raise HTTPException(404, 'Card not found')
    return card


async def filter_card_results(db, results, user_id, id_key='id'):
    ids = [r.get(id_key) for r in results if r.get(id_key)]
    allowed = set((await db.execute(select(Flashcard.id).where(
        Flashcard.id.in_(ids), visible_cards(user_id)))).scalars().all()) if ids else set()
    return [r for r in results if r.get(id_key) in allowed]
