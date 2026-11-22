"""Restore Markdown-only summaries inside the generating Agent's transaction."""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import KnowledgePointNode


async def load_summary_nodes(session, summary, document_id: str, *, persist: bool = True) -> list:
    """Call before any other writes; the Agent owns commit/rollback.

    Stable, summary-specific IDs let concurrent first generators converge. A
    competing committed restoration can be reloaded after our failed flush.
    persist=False stages the same IDs without a write lock during AI calls;
    call again with the default before saving resources in this transaction.
    """
    stmt = select(KnowledgePointNode).where(
        KnowledgePointNode.summary_id == summary.id
    ).order_by(KnowledgePointNode.level, KnowledgePointNode.sequence)
    nodes = (await session.execute(stmt)).scalars().all()
    if nodes:
        return nodes

    from app.core.knowledge.summary_generator import summary_generator

    source_id = summary.document_id or document_id
    parsed = summary_generator.extract_nodes_from_markdown(summary.content_md, source_id)
    remapped = {n["id"]: str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowall:summary:{summary.id}:node:{n['id']}"))
                for n in parsed}
    nodes = [KnowledgePointNode(
        id=remapped[n["id"]], summary_id=summary.id, document_id=source_id,
        parent_id=remapped.get(n.get("parent_id")), level=n["level"], sequence=n["sequence"],
        title=n["title"], explanation=n.get("explanation", ""),
        related_concepts=n.get("related_concepts", ""), examples=n.get("examples", ""),
        tags=n.get("tags") or ([n["tag"]] if n.get("tag") else []),
    ) for n in parsed]
    if not nodes:
        return []
    if not persist:
        return nodes
    for node in nodes:
        session.add(node)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        recovered = (await session.execute(stmt)).scalars().all()
        if not recovered:
            raise
        return recovered
    summary.node_count = len(nodes)
    summary.level_stats = summary_generator._compute_level_stats(parsed)
    return nodes
