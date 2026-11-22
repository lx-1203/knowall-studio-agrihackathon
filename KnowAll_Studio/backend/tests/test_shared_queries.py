"""Contract tests for the two shared query helpers extracted in round 20 (B22).

Background
----------
`pylint --enable=duplicate-code` (R0801) found two genuinely duplicated blocks:

  * **chunk selection** — three byte-identical sites (`app/api/knowledge.py`
    twice, `app/api/questions.py` once),
  * **question → knowledge-point coverage** — two byte-identical 26-line sites
    (`app/api/questions.py`, `app/api/interactive_quiz.py`), differing only in
    the name of the local `Result` variable.

They now live in `app/core/documents.py::load_document_chunks` and
`app/core/knowledge/question_coverage.py::load_knowledge_points_by_question`.

These tests pin the *contract* of each helper — the exact properties that made
the original copies interchangeable — so de-duplication cannot silently change
behaviour:

  * same rows, same order, same dict keys
  * document isolation (one document's chunks never leak into another's)
  * empty/absent input ⇒ **no query at all** (every original site short-circuited
    behind `if x:`), verified by counting statements rather than by inspecting
    the returned value — `[]` and `{}` are also what a wrong-but-quiet query
    returns, so asserting only on the value would not notice the regression
  * query count constant as data grows (the N+1 guard this project uses elsewhere)

Query counting uses a SQLAlchemy `before_cursor_execute` listener, so these tests
are deterministic and CI-safe (no wall-clock timing).
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.documents import load_document_chunks
from app.core.knowledge.question_coverage import load_knowledge_points_by_question
from app.database import Base
from app.models import Document, DocumentChunk, KnowledgeCoverage, KnowledgePointNode

DOC_A = "doc-a"
DOC_B = "doc-b"


@pytest.fixture
async def factory():
    """In-memory SQLite with every table created, plus a sessionmaker."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


def count_selects(engine) -> dict:
    """Count SELECT statements executed against `engine` from now on."""
    counter = {"n": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    return counter


def _engine_of(maker) -> object:
    return maker.kw["bind"]


async def _seed_documents(maker) -> None:
    async with maker() as session:
        session.add(Document(id=DOC_A, filename="a.md", file_type="md", sha256="sha-a"))
        session.add(Document(id=DOC_B, filename="b.md", file_type="md", sha256="sha-b"))
        await session.commit()


async def _seed_chunks(maker, doc_id: str, texts: list[str], indices: list[int]) -> list[str]:
    """Insert chunks and return their ids in insertion (not index) order."""
    ids = []
    async with maker() as session:
        for i, (text, idx) in enumerate(zip(texts, indices)):
            cid = f"{doc_id}-c{i}"
            ids.append(cid)
            session.add(
                DocumentChunk(
                    id=cid,
                    doc_id=doc_id,
                    chunk_index=idx,
                    text_content=text,
                    content_hash=f"{doc_id}-h{i}",
                )
            )
        await session.commit()
    return ids


async def _seed_points(maker, specs: list[tuple[str, int, int, str]]) -> None:
    """specs = [(id, level, sequence, title)]"""
    async with maker() as session:
        for kp_id, level, sequence, title in specs:
            session.add(
                KnowledgePointNode(
                    id=kp_id,
                    document_id=DOC_A,
                    level=level,
                    sequence=sequence,
                    title=title,
                    explanation=f"explanation for {title}",
                )
            )
        await session.commit()


async def _seed_coverage(maker, rows: list[tuple[str, str, str]]) -> None:
    """rows = [(knowledge_point_id, resource_type, resource_id)]"""
    async with maker() as session:
        for kp_id, rtype, rid in rows:
            session.add(
                KnowledgeCoverage(knowledge_point_id=kp_id, resource_type=rtype, resource_id=rid)
            )
        await session.commit()


# ============================================================
# load_document_chunks
# ============================================================

async def test_chunks_all_of_document_ordered_by_chunk_index(factory):
    """No chunk_ids ⇒ every chunk of the document, in chunk_index order."""
    await _seed_documents(factory)
    # Deliberately insert out of order so the assertion tests ORDER BY, not luck.
    await _seed_chunks(factory, DOC_A, ["third", "first", "second"], [3, 1, 2])

    async with factory() as session:
        chunks = await load_document_chunks(session, DOC_A)

    assert [c.text_content for c in chunks] == ["first", "second", "third"]


async def test_chunks_explicit_ids_return_that_subset_only(factory):
    """chunk_ids wins over document_id and is not re-ordered by the helper."""
    await _seed_documents(factory)
    ids = await _seed_chunks(factory, DOC_A, ["c0", "c1", "c2"], [0, 1, 2])
    await _seed_chunks(factory, DOC_B, ["other"], [0])

    async with factory() as session:
        chunks = await load_document_chunks(session, DOC_A, [ids[2], ids[0]])

    assert sorted(c.text_content for c in chunks) == ["c0", "c2"]


async def test_chunks_document_isolation(factory):
    """A document's chunks never leak into another document's result."""
    await _seed_documents(factory)
    await _seed_chunks(factory, DOC_A, ["a0", "a1"], [0, 1])
    await _seed_chunks(factory, DOC_B, ["b0"], [0])

    async with factory() as session:
        a = await load_document_chunks(session, DOC_A)
        b = await load_document_chunks(session, DOC_B)

    assert [c.text_content for c in a] == ["a0", "a1"]
    assert [c.text_content for c in b] == ["b0"]


async def test_chunks_missing_data_returns_empty_and_does_not_raise(factory):
    """Empty document and non-matching ids both yield [] — the caller decides 404.

    This matters because the three original sites raise *three different* 404
    messages; the helper must not pick one of them.
    """
    await _seed_documents(factory)

    async with factory() as session:
        assert await load_document_chunks(session, DOC_A) == []
        assert await load_document_chunks(session, DOC_A, ["does-not-exist"]) == []
        assert await load_document_chunks(session, "no-such-document") == []


async def test_chunks_query_count_is_constant(factory):
    """Exactly one SELECT per call, independent of how many chunks match."""
    await _seed_documents(factory)
    await _seed_chunks(factory, DOC_A, [f"t{i}" for i in range(5)], list(range(5)))

    async with factory() as session:
        counter = count_selects(_engine_of(factory))
        await load_document_chunks(session, DOC_A)
        one = counter["n"]
        counter["n"] = 0
        await load_document_chunks(session, DOC_A, [f"{DOC_A}-c0", f"{DOC_A}-c1"])
        two = counter["n"]

    assert (one, two) == (1, 1)


# ============================================================
# load_knowledge_points_by_question
# ============================================================

async def test_coverage_groups_by_question_in_level_sequence_order(factory):
    await _seed_documents(factory)
    # Insert out of (level, sequence) order on purpose.
    await _seed_points(factory, [
        ("kp-l3", 3, 1, "deep"),
        ("kp-l1", 1, 2, "second root"),
        ("kp-l1a", 1, 1, "first root"),
    ])
    await _seed_coverage(factory, [
        ("kp-l3", "question", "q1"),
        ("kp-l1", "question", "q1"),
        ("kp-l1a", "question", "q1"),
    ])

    async with factory() as session:
        mapping = await load_knowledge_points_by_question(session, ["q1"])

    assert list(mapping) == ["q1"]
    assert [kp["title"] for kp in mapping["q1"]] == ["first root", "second root", "deep"]


async def test_coverage_dict_keys_match_original_shape(factory):
    """The dict shape is a public contract — frontend reads these keys directly."""
    await _seed_documents(factory)
    await _seed_points(factory, [("kp1", 2, 3, "T")])
    await _seed_coverage(factory, [("kp1", "question", "q1")])

    async with factory() as session:
        mapping = await load_knowledge_points_by_question(session, ["q1"])

    assert sorted(mapping["q1"][0]) == [
        "explanation", "id", "level", "parent_id", "related_concepts", "title",
    ]
    assert mapping["q1"][0]["id"] == "kp1"
    assert mapping["q1"][0]["level"] == 2


async def test_coverage_absent_questions_are_omitted_not_empty(factory):
    """Questions with no coverage are absent from the mapping (callers use .get)."""
    await _seed_documents(factory)
    await _seed_points(factory, [("kp1", 1, 1, "T")])
    await _seed_coverage(factory, [("kp1", "question", "q1")])

    async with factory() as session:
        mapping = await load_knowledge_points_by_question(session, ["q1", "q2"])

    assert "q1" in mapping
    assert "q2" not in mapping


async def test_coverage_ignores_non_question_resource_types(factory):
    """Only resource_type == 'question' is attached — flashcards must not leak in."""
    await _seed_documents(factory)
    await _seed_points(factory, [("kp-q", 1, 1, "Q"), ("kp-f", 1, 2, "F")])
    await _seed_coverage(factory, [
        ("kp-q", "question", "r1"),
        ("kp-f", "flashcard", "r1"),
    ])

    async with factory() as session:
        mapping = await load_knowledge_points_by_question(session, ["r1"])

    assert [kp["id"] for kp in mapping["r1"]] == ["kp-q"]


async def test_coverage_empty_input_issues_no_query(factory):
    """`if q_ids:` short-circuit is preserved — an IN () round-trip is wasted work."""
    async with factory() as session:
        counter = count_selects(_engine_of(factory))
        assert await load_knowledge_points_by_question(session, []) == {}
        assert counter["n"] == 0


async def test_coverage_query_count_is_constant(factory):
    """One SELECT for 1 question and still one SELECT for 5 — not an N+1."""
    await _seed_documents(factory)
    await _seed_points(factory, [("kp1", 1, 1, "T")])
    await _seed_coverage(factory, [(f"kp1", "question", f"q{i}") for i in range(5)])

    async with factory() as session:
        counter = count_selects(_engine_of(factory))
        await load_knowledge_points_by_question(session, ["q0"])
        one = counter["n"]
        counter["n"] = 0
        await load_knowledge_points_by_question(session, [f"q{i}" for i in range(5)])
        five = counter["n"]

    assert (one, five) == (1, 1)


# ============================================================
# load_documents_with_chunks  (round-21 N+1 fix)
# ============================================================

async def test_documents_with_chunks_costs_two_queries_regardless_of_count(factory):
    """The whole point of the batched loader: 2 round-trips, not 2 per document.

    Before round 21 ``generate_summary`` issued two SELECTs per requested
    document id, so a 3-document request cost 6 and a 20-document request cost 40.
    """
    async with factory() as session:
        for i in range(6):
            session.add(Document(id=f"doc-{i}", filename=f"{i}.md", file_type="md", sha256=f"sha-{i}"))
        await session.commit()
    for i in range(6):
        await _seed_chunks(factory, f"doc-{i}", [f"t{i}-0", f"t{i}-1"], [0, 1])

    from app.core.documents import load_documents_with_chunks

    async with factory() as session:
        counter = count_selects(_engine_of(factory))
        await load_documents_with_chunks(session, ["doc-0"])
        one = counter["n"]
        counter["n"] = 0
        await load_documents_with_chunks(session, [f"doc-{i}" for i in range(6)])
        six = counter["n"]

    assert (one, six) == (2, 2), "query count must not scale with the document count"


async def test_documents_with_chunks_preserves_requested_order_and_chunk_index(factory):
    """Order is load-bearing: chunk texts are concatenated into an LLM prompt."""
    async with factory() as session:
        for i in range(3):
            session.add(Document(id=f"d{i}", filename=f"f{i}.md", file_type="md", sha256=f"s{i}"))
        await session.commit()
    # insert chunks out of index order to prove ORDER BY is doing work
    await _seed_chunks(factory, "d0", ["c1", "c0"], [1, 0])
    await _seed_chunks(factory, "d2", ["z0"], [0])

    from app.core.documents import load_documents_with_chunks

    async with factory() as session:
        docs, by_doc = await load_documents_with_chunks(session, ["d2", "d0", "d1"])

    assert [d.id for d in docs] == ["d2", "d0", "d1"], "documents must follow the requested order"
    assert [c.text_content for c in by_doc["d0"]] == ["c0", "c1"], "chunks must be by chunk_index"
    assert by_doc.get("d1", []) == [], "a document with no chunks yields no entry"


async def test_documents_with_chunks_skips_missing_ids_without_querying(factory):
    async with factory() as session:
        session.add(Document(id="real", filename="r.md", file_type="md", sha256="sr"))
        await session.commit()

    from app.core.documents import load_documents_with_chunks

    async with factory() as session:
        counter = count_selects(_engine_of(factory))
        docs, by_doc = await load_documents_with_chunks(session, ["real", "ghost"])
        assert counter["n"] == 2

    assert [d.id for d in docs] == ["real"]
    assert by_doc == {}


async def test_documents_with_chunks_empty_input_issues_no_query(factory):
    from app.core.documents import load_documents_with_chunks

    async with factory() as session:
        counter = count_selects(_engine_of(factory))
        docs, by_doc = await load_documents_with_chunks(session, [])
        assert counter["n"] == 0

    assert docs == [] and by_doc == {}


# ============================================================
# CoverageEngine.ensure_full_coverage  (round-21 N+1 fix)
# ============================================================

@pytest.mark.parametrize("n_points", [2, 12])
async def test_ensure_full_coverage_node_lookup_does_not_grow(factory, monkeypatch, n_points):
    """Was one `session.get()` per uncovered point; now one batched `IN` query.

    The generators are stubbed to return nothing so the only DB work being
    measured is the per-point knowledge-point lookup.
    """
    from app.core.memory import coverage as cov_module
    from app.core.memory import card_generator
    from app.core.memory.coverage import CoverageEngine
    from app.core.quiz import quiz_generator

    await _seed_documents(factory)
    async with factory() as session:
        for i in range(n_points):
            session.add(
                KnowledgePointNode(
                    id=f"kp-{i}",
                    document_id=DOC_A,
                    level=1,
                    sequence=i,
                    title=f"t{i}",
                    explanation=f"e{i}",
                )
            )
        await session.commit()

    async def _no_questions(**_kwargs):
        return []

    async def _no_cards(**_kwargs):
        return []

    monkeypatch.setattr(card_generator, "generate_cards", _no_cards)
    monkeypatch.setattr(quiz_generator, "generate_questions", _no_questions)
    monkeypatch.setattr(cov_module, "async_session", factory)

    engine = CoverageEngine()

    async def _fake_calculate(self, summary_id, user_id="local_user"):
        return {"uncovered_points": [{"id": f"kp-{i}"} for i in range(n_points)]}

    monkeypatch.setattr(CoverageEngine, "calculate", _fake_calculate)

    counter = count_selects(_engine_of(factory))
    await engine.ensure_full_coverage("summary-1", DOC_A)
    # One SELECT for the batched node lookup — independent of n_points.
    # (The old code issued n_points SELECTs here: 2 and 12 respectively.)
    assert counter["n"] == 1, f"{n_points} uncovered points issued {counter['n']} SELECTs"
