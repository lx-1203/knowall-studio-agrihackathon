import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from app.api.glossary import (
    list_terms, get_term, suggest_terms, get_term_variants,
    sync_glossary, get_stats, SyncRequest,
)
from app.models import GlossaryTerm


@pytest.mark.asyncio
async def test_list_terms_returns_items(mock_db_session):
    mock_db_session.execute.return_value = MagicMock(
        scalar_one=MagicMock(return_value=2),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
    )
    resp = await list_terms(db=mock_db_session, page=1, page_size=20)
    assert resp["total"] == 2


@pytest.mark.asyncio
async def test_list_terms_allows_large_page_size(mock_db_session):
    """前端一次性拉取全量词库（page_size=2000）不应触发 422。"""
    mock_db_session.execute.return_value = MagicMock(
        scalar_one=MagicMock(return_value=5),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
    )
    resp = await list_terms(db=mock_db_session, page=1, page_size=2000)
    assert resp["page_size"] == 2000


@pytest.mark.asyncio
async def test_get_term_not_found_raises(mock_db_session):
    mock_db_session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    )
    with pytest.raises(HTTPException) as exc_info:
        await get_term("不存在的术语", db=mock_db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_term_by_alias_hit(mock_db_session):
    term_row = GlossaryTerm(
        term="PCR", subject="分子生物学",
        definition="聚合酶链式反应", aliases=["聚合酶链式反应"],
    )
    first = MagicMock(
        scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    )
    second = MagicMock(
        scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=term_row)))
    )
    mock_db_session.execute.side_effect = [first, second]
    resp = await get_term("PCR", db=mock_db_session)
    assert resp["term"] == "PCR"
    assert resp["subject"] == "分子生物学"
    assert resp["definition"] == "聚合酶链式反应"


@pytest.mark.asyncio
async def test_get_term_multiple_rows_no_subject_returns_first(mock_db_session):
    """回归：同名词条多条且未传 subject 时返回第一条，而不是 500（MultipleResultsFound）。"""
    from sqlalchemy.exc import MultipleResultsFound

    row1 = GlossaryTerm(term="PCR", subject="分子生物学", definition="d1")
    result = MagicMock()
    # 旧实现调用 scalar_one_or_none 会因 2 行抛 MultipleResultsFound
    result.scalar_one_or_none.side_effect = MultipleResultsFound("2 rows")
    result.scalars.return_value.first.return_value = row1
    mock_db_session.execute.return_value = result
    resp = await get_term("PCR", db=mock_db_session)
    assert resp["term"] == "PCR"
    assert resp["subject"] == "分子生物学"


@pytest.mark.asyncio
async def test_suggest_terms_returns_list(mock_db_session):
    mock_db_session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=["复制子"])))
    )
    resp = await suggest_terms("复制", db=mock_db_session)
    assert resp["suggestions"] == ["复制子"]


@pytest.mark.asyncio
async def test_get_term_variants_empty(mock_db_session):
    mock_db_session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )
    resp = await get_term_variants("PCR", db=mock_db_session)
    assert resp["items"] == []


@pytest.mark.asyncio
async def test_get_term_variants_by_term(mock_db_session):
    row1 = GlossaryTerm(term="PCR", subject="分子生物学", definition="d1")
    row2 = GlossaryTerm(term="PCR", subject="医学", definition="d2")
    mock_db_session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row1, row2])))
    )
    resp = await get_term_variants("PCR", db=mock_db_session)
    assert len(resp["items"]) == 2
    assert resp["items"][0]["term"] == "PCR"


@pytest.mark.asyncio
async def test_sync_endpoint(mock_db_session):
    fake_sync = AsyncMock(return_value={"added": 1, "updated": 2, "total": 3})
    with patch("app.api.glossary.sync_glossary_from_db", fake_sync):
        resp = await sync_glossary(
            req=SyncRequest(subject="分子生物学"),
            db=mock_db_session,
        )
    assert resp == {"ok": True, "added": 1, "updated": 2, "total": 3}
    fake_sync.assert_awaited_once_with(mock_db_session, subject="分子生物学", summary_id=None)


@pytest.mark.asyncio
async def test_stats(mock_db_session):
    mock_db_session.execute.return_value = MagicMock(
        scalar_one=MagicMock(return_value=7),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=["综合", "分子生物学"]))),
    )
    resp = await get_stats(db=mock_db_session)
    assert resp["total"] == 7
    assert resp["subjects"] == ["分子生物学", "综合"]
    assert resp["snapshot_path"] == "data/glossary_terms.kgb"


@pytest.mark.asyncio
async def test_like_escape_escapes_sql_wildcards():
    """`%`, `_` and backslash are escaped before they reach LIKE."""
    from app.api.glossary import _like_escape

    assert _like_escape("50%_x\\y") == "50\\%\\_x\\\\y"


def test_to_dict_defaults_missing_source_type_to_document():
    """A term without source_type is reported as a document-sourced term."""
    from app.api.glossary import _to_dict
    from app.models import GlossaryTerm

    row = GlossaryTerm(term="PCR", subject="分子生物学", definition="聚合酶链式反应")
    row.source_type = None
    assert _to_dict(row)["source_type"] == "document"


@pytest.mark.asyncio
async def test_list_terms_applies_offset_and_limit(mock_db_session):
    """page=2 / page_size=50 must become LIMIT 50 OFFSET 50."""
    mock_db_session.execute.return_value = MagicMock(
        scalar_one=MagicMock(return_value=0),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
    )
    await list_terms(db=mock_db_session, page=2, page_size=50)

    rows_stmt = mock_db_session.execute.await_args_list[1].args[0]
    compiled = str(rows_stmt.compile(compile_kwargs={"literal_binds": True})).upper()
    assert "LIMIT 50" in compiled
    assert "OFFSET 50" in compiled


@pytest.mark.asyncio
async def test_suggest_terms_query_is_distinct(mock_db_session):
    """Suggestion queries must de-duplicate terms (DISTINCT)."""
    mock_db_session.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=["复制子"])))
    )
    resp = await suggest_terms(q="复制", db=mock_db_session)

    stmt = mock_db_session.execute.await_args_list[0].args[0]
    assert "DISTINCT" in str(stmt.compile()).upper()
    assert resp["suggestions"] == ["复制子"]


@pytest.mark.asyncio
async def test_stats_subject_query_is_distinct(mock_db_session):
    """The subject list must come from a DISTINCT query, not raw rows."""
    mock_db_session.execute.return_value = MagicMock(
        scalar_one=MagicMock(return_value=3),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=["综合"]))),
    )
    await get_stats(db=mock_db_session)

    stmt = mock_db_session.execute.await_args_list[1].args[0]
    assert "DISTINCT" in str(stmt.compile()).upper()

