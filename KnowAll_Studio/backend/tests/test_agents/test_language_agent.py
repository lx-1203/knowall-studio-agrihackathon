"""Tests for LanguageAgent - vocabulary extraction from language learning materials."""
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Ensure backend is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.agents.base import AgentResult


def _patch_async_session(session):
    """Patch app.database.async_session() to yield `session`."""
    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc_info):
            return False

    return patch("app.database.async_session", _Ctx)


def _fake_doc(filename):
    doc = MagicMock()
    doc.filename = filename
    return doc


def _fake_chunk(text):
    chunk = MagicMock()
    chunk.text_content = text
    return chunk


def _fake_session(doc, chunks=None):
    session = AsyncMock()
    session.get = AsyncMock(return_value=doc)
    scalars = MagicMock()
    scalars.all.return_value = chunks or []
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    return session


class TestLanguageAgentShouldRun:
    """Tests for LanguageAgent.should_run() method."""

    def test_should_run_returns_true_for_english(self):
        """should_run() returns True when language_type is 'english'."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent.should_run("doc_1", language_type="english")
        assert result is True

    def test_should_run_returns_true_for_japanese(self):
        """should_run() returns True when language_type is 'japanese'."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent.should_run("doc_1", language_type="japanese")
        assert result is True

    def test_should_run_returns_false_for_non_language_document(self):
        """should_run() returns False for non-language documents."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        # language_type is neither a supported language nor 'auto'
        result = agent.should_run("doc_1", language_type="math")
        assert result is False

    def test_should_run_returns_false_with_no_language_type(self):
        """should_run() returns False when no language_type is provided at all and auto-detect fails."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        with patch.object(agent, "_auto_detect_language_material", return_value=False):
            result = agent.should_run("doc_1")
        assert result is False

    def test_should_run_uses_auto_detect_when_language_type_is_auto(self):
        """language_type='auto' delegates to _auto_detect_language_material()."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        with patch.object(agent, "_auto_detect_language_material", return_value=True) as detect:
            assert agent.should_run("doc_1", language_type="auto") is True
            assert agent.should_run("doc_2") is True
        assert detect.call_count == 2


    @pytest.mark.asyncio
    async def test_should_run_async_detects_language_material_inside_event_loop(self):
        """should_run_async() works inside a running loop (the orchestrator path)."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        session = _fake_session(_fake_doc("english_vocab.pdf"))

        with _patch_async_session(session):
            assert await agent.should_run_async("doc_1", language_type="auto") is True
            assert await agent.should_run_async("doc_2") is True

    @pytest.mark.asyncio
    async def test_should_run_async_handles_explicit_and_unknown_language_types(self):
        """Explicit supported types short-circuit; unknown types return False."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        assert await agent.should_run_async("doc_1", language_type="japanese") is True
        assert await agent.should_run_async("doc_1", language_type="math") is False

class TestLanguageAgentAutoDetect:
    """Tests for LanguageAgent._auto_detect_language_material()."""

    def test_auto_detect_returns_true_for_filename_hint(self):
        """A filename containing a language hint marks the document as language material."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        session = _fake_session(_fake_doc("english_vocab.pdf"))

        with _patch_async_session(session):
            assert agent._auto_detect_language_material("doc_1") is True

    def test_auto_detect_returns_true_for_latin_dominated_chunks(self):
        """Chunks whose Latin ratio exceeds 40% mark the document as language material."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        session = _fake_session(
            _fake_doc("unknown.pdf"), [_fake_chunk("hello world this is english")]
        )

        with _patch_async_session(session):
            assert agent._auto_detect_language_material("doc_1") is True

    def test_auto_detect_returns_false_for_chinese_only_chunks(self):
        """CJK-only chunks stay below the Latin ratio, so detection returns False."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        session = _fake_session(
            _fake_doc("unknown.pdf"), [_fake_chunk("纯中文内容没有任何拉丁字符")]
        )

        with _patch_async_session(session):
            assert agent._auto_detect_language_material("doc_1") is False

    def test_auto_detect_returns_false_when_document_is_missing(self):
        """A document that does not exist is not language material."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        session = _fake_session(None)

        with _patch_async_session(session):
            assert agent._auto_detect_language_material("doc_1") is False

    def test_auto_detect_latin_ratio_threshold_is_strict(self):
        """Exactly 40% Latin characters is not enough; 50% is."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        exactly_40_percent = "abcd" + "012345"
        exactly_50_percent = "abcde" + "01234"

        with _patch_async_session(
            _fake_session(_fake_doc("unknown.pdf"), [_fake_chunk(exactly_40_percent)])
        ):
            assert agent._auto_detect_language_material("doc_1") is False

        with _patch_async_session(
            _fake_session(_fake_doc("unknown.pdf"), [_fake_chunk(exactly_50_percent)])
        ):
            assert agent._auto_detect_language_material("doc_1") is True

    def test_auto_detect_samples_only_first_500_chars_of_each_chunk(self):
        """Only the first 500 characters of a chunk feed the Latin-ratio check."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        text = "a" * 100 + "0" * 900
        session = _fake_session(_fake_doc("unknown.pdf"), [_fake_chunk(text)])

        with _patch_async_session(session):
            assert agent._auto_detect_language_material("doc_1") is False

    def test_auto_detect_samples_only_first_three_chunks(self):
        """The chunk query is bounded to the first three chunks."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        session = _fake_session(_fake_doc("unknown.pdf"), [])

        with _patch_async_session(session):
            agent._auto_detect_language_material("doc_1")

        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 3" in compiled.upper()

    def test_auto_detect_handles_session_error_gracefully(self):
        """Any exception while opening the session is swallowed into False."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        class _BoomCtx:
            async def __aenter__(self):
                raise RuntimeError("DB down")

            async def __aexit__(self, *exc_info):
                return False

        with patch("app.database.async_session", _BoomCtx):
            assert agent._auto_detect_language_material("doc_1") is False
class TestLanguageAgentEstimateDifficulty:
    """Tests for LanguageAgent._estimate_difficulty()."""

    def test_estimate_difficulty_returns_hard_for_long_words(self):
        """_estimate_difficulty() returns 'hard' for words >= 12 chars."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent._estimate_difficulty({"word": "antidisestablishmentarianism", "part_of_speech": "noun"})
        assert result == "hard"

    def test_estimate_difficulty_returns_easy_for_short_common_words(self):
        """_estimate_difficulty() returns 'easy' for short common words."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent._estimate_difficulty({"word": "cat", "part_of_speech": "noun"})
        assert result == "easy"

    def test_estimate_difficulty_returns_medium_as_default(self):
        """_estimate_difficulty() returns 'medium' as default for normal-length words."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent._estimate_difficulty({"word": "vocabulary", "part_of_speech": "noun"})
        assert result == "medium"

    def test_estimate_difficulty_short_non_common_pos_returns_medium(self):
        """_estimate_difficulty() returns 'medium' for short word with uncommon POS."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent._estimate_difficulty({"word": "hi", "part_of_speech": "interjection"})
        assert result == "medium"

    def test_estimate_difficulty_empty_word_returns_easy_if_short(self):
        """_estimate_difficulty() handles empty word -- returns 'easy' if len <= 4 and POS matches."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        result = agent._estimate_difficulty({"word": "", "part_of_speech": "verb"})
        assert result == "easy"

    def test_estimate_difficulty_twelve_char_word_is_hard(self):
        """A word of exactly 12 characters is 'hard' (threshold is >= 12)."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        assert agent._estimate_difficulty({"word": "abcdefghijkl"}) == "hard"

    def test_estimate_difficulty_four_char_noun_is_easy(self):
        """A 4-character noun is 'easy' (threshold is <= 4)."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        assert agent._estimate_difficulty({"word": "word", "part_of_speech": "noun"}) == "easy"

    def test_estimate_difficulty_five_char_noun_is_medium(self):
        """A 5-character noun is past the easy threshold and stays 'medium'."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()
        assert agent._estimate_difficulty({"word": "words", "part_of_speech": "noun"}) == "medium"


class TestLanguageAgentRun:
    """Tests for LanguageAgent.run() method."""

    @pytest.mark.asyncio
    async def test_run_with_missing_summary_returns_error(self):
        """run() with missing summary returns error AgentResult."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)

        with patch("app.database.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await agent.run(summary_id="bad_sum", document_id="doc_1")

        assert result.status == "error"
        assert "Summary not found" in result.error

    @pytest.mark.asyncio
    async def test_run_parses_valid_llm_json_response(self):
        """run() with valid LLM JSON response parses and stores vocab."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        vocab_json = '[{"word":"hello","phonetic":"həˈloʊ","part_of_speech":"interjection","definition":"你好","example_sentence":"Hello, how are you?"},{"word":"world","phonetic":"wɜːld","part_of_speech":"noun","definition":"世界","example_sentence":"The world is beautiful."}]'

        mock_summary = MagicMock()
        mock_summary.document_id = "doc_1"
        mock_summary.document_ids = ["doc_1"]
        mock_summary.content_md = "# Test Content"

        mock_node = MagicMock()
        mock_node.title = "Topic"
        mock_node.explanation = "Some explanation here for testing"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_summary)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_node]

        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_llm_result = MagicMock()
        mock_llm_result.content = vocab_json

        with patch("app.database.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("app.core.api_scheduler.api_client") as mock_api:
                mock_api.generate = AsyncMock(return_value=mock_llm_result)
                result = await agent.run(summary_id="sum_1", document_id="doc_1")

        assert result.status == "success"
        assert result.result["total_words"] == 2
        assert len(result.result["words"]) == 2
        assert result.result["words"][0]["word"] == "hello"
        assert result.result["words"][1]["word"] == "world"
        # the knowledge text must reach the LLM untruncated (300-char cap not hit here)
        generate_kwargs = mock_api.generate.call_args.kwargs
        assert generate_kwargs["messages"][1]["content"] == "Topic: Some explanation here for testing"
        assert generate_kwargs["generation_content"] == "Topic: Some explanation here for testing"

    @pytest.mark.asyncio
    async def test_run_non_json_llm_response_falls_back_to_regex(self):
        """run() with non-JSON LLM response falls back to regex extraction."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        llm_content = 'Here are some words: [{"word":"test","phonetic":"tɛst","part_of_speech":"noun","definition":"测试","example_sentence":"This is a test."}] done.'

        mock_summary = MagicMock()
        mock_summary.document_id = "doc_1"
        mock_summary.document_ids = ["doc_1"]
        mock_summary.content_md = "# Test Content"
        mock_node = MagicMock()
        mock_node.title = "Topic"
        mock_node.explanation = "Some explanation"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_summary)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_node]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_llm_result = MagicMock()
        mock_llm_result.content = llm_content

        with patch("app.database.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("app.core.api_scheduler.api_client") as mock_api:
                mock_api.generate = AsyncMock(return_value=mock_llm_result)
                result = await agent.run(summary_id="sum_1", document_id="doc_1")

        assert result.status == "success"
        assert result.result["total_words"] == 1
        assert result.result["words"][0]["word"] == "test"

    @pytest.mark.asyncio
    async def test_run_completely_unparseable_content_returns_error(self):
        """A broken model response must not appear to be successful generation."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        mock_summary = MagicMock()
        mock_summary.document_id = "doc_1"
        mock_summary.document_ids = ["doc_1"]
        mock_summary.content_md = "# Test"
        mock_node = MagicMock()
        mock_node.title = "Topic"
        mock_node.explanation = "Explanation"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_summary)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_node]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_llm_result = MagicMock()
        mock_llm_result.content = "Just some plain text, no JSON at all."

        with patch("app.database.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("app.core.api_scheduler.api_client") as mock_api:
                mock_api.generate = AsyncMock(return_value=mock_llm_result)
                result = await agent.run(summary_id="sum_1", document_id="doc_1")

        assert result.status == "error"
        assert result.error
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_filters_out_items_with_blank_words(self):
        """run() skips blank/whitespace-only words and keeps the rest with computed difficulty."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        vocab_json = '[{"word":"valid","phonetic":"","part_of_speech":"","definition":"ok","example_sentence":""},{"word":"   ","phonetic":"","part_of_speech":"","definition":"","example_sentence":""},{"word":"antidisestablishmentarianism","phonetic":"","part_of_speech":"noun","definition":"long word","example_sentence":""}]'

        mock_summary = MagicMock()
        mock_summary.document_id = "doc_1"
        mock_summary.document_ids = ["doc_1"]
        mock_summary.content_md = "# Test"
        mock_node = MagicMock()
        mock_node.title = "Topic"
        mock_node.explanation = "Explanation"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_summary)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_node]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_llm_result = MagicMock()
        mock_llm_result.content = vocab_json

        with patch("app.database.async_session") as mock_session_ctx:
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("app.core.api_scheduler.api_client") as mock_api:
                mock_api.generate = AsyncMock(return_value=mock_llm_result)
                result = await agent.run(summary_id="sum_1", document_id="doc_1")

        assert result.status == "success"
        assert result.result["total_words"] == 2
        assert result.result["words"][1]["word"] == "antidisestablishmentarianism"
        assert result.result["words"][0]["word"] == "valid"
        assert result.result["words"][1]["difficulty"] == "hard"

    @pytest.mark.asyncio
    async def test_run_handles_db_exception_gracefully(self):
        """run() handles DB exception gracefully by returning error result."""
        from app.core.agents.language_agent import LanguageAgent

        agent = LanguageAgent()

        with patch("app.database.async_session") as mock_session_ctx:
            mock_session_ctx.side_effect = RuntimeError("Database connection lost")

            result = await agent.run(summary_id="sum_1", document_id="doc_1")

        assert result.status == "error"
        assert "Database connection lost" in result.error
