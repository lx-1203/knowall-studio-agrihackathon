"""Query-count regression tests for MindMapAgent._load_cross_edges().

Why this file exists
--------------------
The method loads every KnowledgeTree, filters them in Python by doc_ids, and
then issued **one query per matching tree** to fetch its KnowledgeEdges:

    for tree in trees:
        if not doc_id_set.intersection(tree.doc_ids or []):
            continue
        edge_result = await session.execute(
            select(KnowledgeEdge).where(KnowledgeEdge.tree_id == tree.id)
        )

It is now one IN query for all matching trees.

Order is preserved exactly: the original emitted edges tree-by-tree (in the
order trees were loaded, and within a tree in database order). The refactor
re-groups edges by tree_id in Python, so the flat list comes out in the same
sequence — the functional test pins that.
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.agents.mindmap_agent import MindMapAgent
from app.database import Base
from app.models import KnowledgeEdge, KnowledgeTree


@pytest.fixture
async def sqlite_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


async def _seed(factory, n_matching: int, n_non_matching: int = 2, edges_per_tree: int = 2) -> None:
    async with factory() as session:
        for i in range(n_non_matching):
            session.add(
                KnowledgeTree(
                    id=f"other_{i}",
                    name=f"无关树{i}",
                    doc_ids=["other-doc"],
                    tree_data={},
                )
            )
        for i in range(n_matching):
            tid = f"tree_{i}"
            session.add(
                KnowledgeTree(id=tid, name=f"树{i}", doc_ids=["doc-A"], tree_data={})
            )
            for j in range(edges_per_tree):
                session.add(
                    KnowledgeEdge(
                        id=f"e_{i}_{j}",
                        tree_id=tid,
                        source_node_id=f"src_{i}_{j}",
                        target_node_id=f"tgt_{i}_{j}",
                        relation_type="related_to",
                    )
                )
        await session.commit()


def _record_selects(engine):
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    return counter, detach


async def _load(engine, factory, n_matching: int):
    await _seed(factory, n_matching)
    counter, detach = _record_selects(engine)
    try:
        async with factory() as session:
            edges = await MindMapAgent()._load_cross_edges(session, ["doc-A"])
    finally:
        detach()
    return edges, counter["n"]


async def test_cross_edges_only_from_matching_trees(sqlite_factory):
    """Functional check: only trees containing the document contribute edges."""
    engine, factory = sqlite_factory
    edges, _ = await _load(engine, factory, n_matching=2)

    assert len(edges) == 4, edges
    assert {e["source"] for e in edges} == {"src_0_0", "src_0_1", "src_1_0", "src_1_1"}


async def test_cross_edges_preserve_tree_then_db_order(sqlite_factory):
    """Edges must still come out tree-by-tree, in the original sequence."""
    engine, factory = sqlite_factory
    edges, _ = await _load(engine, factory, n_matching=3)

    assert [e["source"] for e in edges] == [
        "src_0_0", "src_0_1",
        "src_1_0", "src_1_1",
        "src_2_0", "src_2_1",
    ]


async def test_cross_edges_query_count_is_flat(sqlite_factory):
    """SELECT count must not scale with the number of matching trees."""
    engine, factory = sqlite_factory
    _, small = await _load(engine, factory, n_matching=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, large = await _load(engine2, factory2, n_matching=40)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < small <= 2, (
        f"2 matching trees issued {small} SELECTs; expected within budget (trees + batched edges)"
    )
    assert 0 < large <= 2, (
        f"40 matching trees issued {large} SELECTs; expected within budget "
        f"(the old per-tree lookup would issue 1 + 40 = 41)"
    )
    assert large == small, (
        f"SELECT count grew with tree count ({small} -> {large}): "
        f"the per-tree KnowledgeEdge lookup is back inside the loop"
    )
