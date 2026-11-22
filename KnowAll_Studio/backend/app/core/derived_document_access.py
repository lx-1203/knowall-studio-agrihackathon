"""Document ownership inherited by persisted learning resources.

Only documents with explicit knowledge_owner_id restrict access. Read current
database ownership in batches, rather than trusting cached ORM identities.
"""
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Document, DocumentChunk, KnowledgeSummary, KnowledgeTree, KnowledgePointNode, QuestionBank


def _summary_documents(summary):
    return set(summary.document_ids or []) | ({summary.document_id} if summary.document_id else set())


async def visible_derived_resources(db, resources, user_id=None):
    resources = list(resources)
    if not resources:
        return []
    document_ids = {id(resource): set() for resource in resources}
    blocked = set()
    summaries = [resource for resource in resources if isinstance(resource, KnowledgeSummary)]
    nodes = [resource for resource in resources if isinstance(resource, KnowledgePointNode)]
    questions = [resource for resource in resources if isinstance(resource, QuestionBank)]
    summary_ids = {node.summary_id for node in nodes if node.summary_id}
    summary_by_id = {}
    if summary_ids:
        summary_by_id = {s.id: s for s in (await db.scalars(select(KnowledgeSummary).where(KnowledgeSummary.id.in_(summary_ids)).execution_options(populate_existing=True))).all()}
    for summary in summaries:
        document_ids[id(summary)] = _summary_documents(summary)
    for resource in resources:
        if isinstance(resource, KnowledgeTree):
            document_ids[id(resource)] = set(resource.doc_ids or [])
    for node in nodes:
        document_ids[id(node)] = {node.document_id} if node.document_id else set()
        if node.summary_id in summary_by_id:
            document_ids[id(node)].update(_summary_documents(summary_by_id[node.summary_id]))
        elif node.summary_id:
            blocked.add(id(node))
    # Variant questions inherit the original's private document even if older
    # generation code did not copy source_chunk_id onto the variant row.
    question_by_id = {question.id: question for question in questions}
    pending = {q.parent_question_id for q in questions if q.parent_question_id} - question_by_id.keys()
    while pending:
        parents = (await db.scalars(select(QuestionBank).where(QuestionBank.id.in_(pending)).execution_options(populate_existing=True))).all()
        question_by_id.update((q.id, q) for q in parents)
        pending = {q.parent_question_id for q in parents if q.parent_question_id} - question_by_id.keys()
    chunk_ids = {q.source_chunk_id for q in question_by_id.values() if q.source_chunk_id}
    chunk_documents = dict((await db.execute(select(DocumentChunk.id, DocumentChunk.doc_id).where(DocumentChunk.id.in_(chunk_ids)))).all()) if chunk_ids else {}
    for question in questions:
        current, seen = question, set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            owner = (current.review_scores or {}).get('knowledge_owner_id')
            if owner and (not user_id or owner != user_id):
                blocked.add(id(question))
            if current.source_chunk_id in chunk_documents:
                document_ids[id(question)].add(chunk_documents[current.source_chunk_id])
            elif current.source_chunk_id:
                blocked.add(id(question))
            if current.parent_question_id and current.parent_question_id not in question_by_id:
                blocked.add(id(question))
            current = question_by_id.get(current.parent_question_id)
    all_ids = set().union(*document_ids.values())
    owners = dict((await db.execute(select(Document.id, Document.metadata_).where(Document.id.in_(all_ids)))).all()) if all_ids else {}
    def allowed(resource):
        if id(resource) in blocked:
            return False
        for doc_id in document_ids[id(resource)]:
            if doc_id not in owners:
                return False
            owner = (owners.get(doc_id) or {}).get('knowledge_owner_id')
            if owner and (not user_id or owner != user_id):
                return False
        return True
    return [resource for resource in resources if allowed(resource)]


async def require_derived_access(db, resource, user_id=None):
    if resource is None or not await visible_derived_resources(db, [resource], user_id):
        raise HTTPException(404, 'Resource not found')
    return resource


async def visible_question_knowledge_points(db, mapping, user_id=None):
    ids = {node['id'] for nodes in mapping.values() for node in nodes}
    if not ids:
        return mapping
    nodes = (await db.scalars(select(KnowledgePointNode).where(KnowledgePointNode.id.in_(ids)).execution_options(populate_existing=True))).all()
    visible_ids = {node.id for node in await visible_derived_resources(db, nodes, user_id)}
    return {qid: [node for node in linked if node['id'] in visible_ids] for qid, linked in mapping.items()}
