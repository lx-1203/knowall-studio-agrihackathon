"""Reuse compiled subject collections as public central concepts, without AI."""
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.knowledge_collections import builtin_collections


async def seed_compiled_concepts(db) -> None:
    """Insert missing compiled concepts only; preserve candidates and contributions.

    A primary-key conflict from simultaneous first visitors is ignored rather
    than turning one visitor's successful request into an error. Caller owns
    the transaction. No source material or personal state enters the seed.
    """
    from app.core.knowledge_network import canonical_identity
    from app.models.knowledge_network import NetworkConcept, ConceptRelation

    rows = {}
    collections = builtin_collections()
    for collection in collections:
        for point in collection['points']:
            identity = canonical_identity(collection['subject'], point['topic'], point['name'])
            rows.setdefault(identity, {
                'id': identity, 'name': point['name'], 'subject': collection['subject'],
                'topic': point['topic'], 'public_content': {**point, 'id': identity}, 'review_status': 'compiled',
            })
    if not rows:
        return
    existing = set()
    identities = list(rows)
    for start in range(0, len(identities), 100):
        existing.update((await db.execute(select(NetworkConcept.id).where(
            NetworkConcept.id.in_(identities[start:start + 100])))).scalars())
    missing = [row for identity, row in rows.items() if identity not in existing]
    dialect = db.get_bind().dialect.name
    for start in range(0, len(missing), 100):
        batch = missing[start:start + 100]
        if dialect == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
            statement = insert(NetworkConcept).values(batch).on_conflict_do_nothing(index_elements=['id'])
            await db.execute(statement)
        elif dialect == 'mysql':
            from sqlalchemy.dialects.mysql import insert
            statement = insert(NetworkConcept).values(batch)
            await db.execute(statement.on_duplicate_key_update(id=statement.inserted.id))
        else:
            for row in batch:
                try:
                    async with db.begin_nested():
                        db.add(NetworkConcept(**row))
                        await db.flush()
                except IntegrityError:
                    if await db.get(NetworkConcept, row['id']) is None:
                        raise

    # Only explicit links with one unambiguous target in the same collection.
    public_ids = set((await db.scalars(select(NetworkConcept.id).where(
        NetworkConcept.public_content.is_not(None)))).all())
    existing_links = set((await db.execute(select(
        ConceptRelation.source_id, ConceptRelation.target_id, ConceptRelation.relation_type
    ).where(ConceptRelation.user_id == 'compiled-library'))).all())
    for collection in collections:
        by_name = {}
        for point in collection['points']:
            cid = canonical_identity(collection['subject'], point['topic'], point['name'])
            by_name.setdefault(point['name'].strip().casefold(), set()).add(cid)
        for point in collection['points']:
            source_id = canonical_identity(collection['subject'], point['topic'], point['name'])
            for field, relation_type in [('prerequisites', 'prerequisite'), ('related', 'related')]:
                for name in point.get(field, []):
                    targets = by_name.get(name.strip().casefold(), set())
                    if len(targets) != 1:
                        continue
                    target_id = next(iter(targets))
                    key = (source_id, target_id, relation_type)
                    if source_id == target_id or not {source_id, target_id} <= public_ids or key in existing_links:
                        continue
                    row = dict(id=hashlib.sha256('\0'.join(key).encode()).hexdigest()[:36],
                               user_id='compiled-library', source_id=source_id, target_id=target_id,
                               relation_type=relation_type, is_public=True, reviewed=True)
                    if dialect == 'sqlite':
                        from sqlalchemy.dialects.sqlite import insert
                        await db.execute(insert(ConceptRelation).values(row).on_conflict_do_nothing())
                    elif dialect == 'mysql':
                        from sqlalchemy.dialects.mysql import insert
                        statement = insert(ConceptRelation).values(row)
                        await db.execute(statement.on_duplicate_key_update(id=statement.inserted.id))
                    else:
                        try:
                            async with db.begin_nested():
                                db.add(ConceptRelation(**row))
                                await db.flush()
                        except IntegrityError:
                            if await db.get(ConceptRelation, row['id']) is None:
                                raise
                    existing_links.add(key)
