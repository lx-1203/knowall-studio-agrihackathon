"""Language Learning Agent - extracts vocabulary from language learning materials"""
import logging
from app.core.agents.base import BaseAgent, AgentRegistry, AgentResult

logger = logging.getLogger(__name__)

# Unicode ranges for language detection
CJK_RANGE = (0x4E00, 0x9FFF)
HIRAGANA_RANGE = (0x3040, 0x309F)
KATAKANA_RANGE = (0x30A0, 0x30FF)
LATIN_RANGE = (0x0041, 0x007A)


@AgentRegistry.register("language")
class LanguageAgent(BaseAgent):
    """Extracts vocabulary items from language learning documents.

    Activated only when the document is detected as language learning material
    (English, Japanese, etc.).
    """

    name = "language"
    description = "检测语言学习材料并提取生词表（单词、音标、释义、例句、词性）"

    def should_run(self, document_id: str, **kwargs) -> bool:
        """Check if this is a language learning document."""
        language_type = kwargs.get("language_type", "auto")

        if language_type in ("english", "japanese", "korean", "french", "german"):
            return True

        # Auto-detect from chunks
        if language_type == "auto":
            return self._auto_detect_language_material(document_id)

        return False

    async def _detect_language_material_async(self, document_id: str) -> bool:
        """Detect language learning material on the caller's event loop.

        This is the production path: the orchestrator runs inside the app event
        loop and calls should_run_async(). The sync wrapper below cannot work
        there, because asyncio.run() refuses to start inside a running loop.
        """
        from app.database import async_session
        from app.models import Document, DocumentChunk
        from sqlalchemy import select

        async with async_session() as session:
            doc = await session.get(Document, document_id)
            if not doc:
                return False

            # Check filename for language hints
            filename_lower = doc.filename.lower()
            lang_hints = ["英语", "english", "日语", "japanese", "语法", "grammar",
                          "词汇", "vocabulary", "单词", "word", "韩语", "korean",
                          "法语", "french", "德语", "german"]
            for hint in lang_hints:
                if hint in filename_lower:
                    return True

            # Check first chunk for language indicators
            stmt = select(DocumentChunk).where(
                DocumentChunk.doc_id == document_id
            ).order_by(DocumentChunk.chunk_index).limit(3)
            result = await session.execute(stmt)
            chunks = result.scalars().all()

            if chunks:
                combined = " ".join(c.text_content[:500] for c in chunks)
                # Count Latin words vs CJK characters
                latin_count = sum(1 for c in combined if LATIN_RANGE[0] <= ord(c) <= LATIN_RANGE[1])
                total_chars = len(combined)
                if total_chars > 0 and latin_count / total_chars > 0.4:
                    return True

            return False

    def _auto_detect_language_material(self, document_id: str) -> bool:
        """Sync wrapper around _detect_language_material_async().

        Only usable outside a running event loop; async callers must use
        should_run_async() instead.
        """
        import asyncio
        try:
            return asyncio.run(self._detect_language_material_async(document_id))
        except Exception as e:
            logger.warning(f"Language auto-detection failed: {e}")
            return False

    async def should_run_async(self, document_id: str, **kwargs) -> bool:
        """Async-safe activation check (see BaseAgent.should_run_async)."""
        language_type = kwargs.get("language_type", "auto")

        if language_type in ("english", "japanese", "korean", "french", "german"):
            return True

        if language_type == "auto":
            try:
                return await self._detect_language_material_async(document_id)
            except Exception as e:
                logger.warning(f"Language auto-detection failed: {e}")
                return False

        return False

    async def run(self, summary_id: str, document_id: str, **kwargs) -> AgentResult:
        from app.database import async_session
        from app.models import Document, KnowledgeSummary, KnowledgePointNode, LanguageVocabulary
        from app.core.api_scheduler import api_client, TaskType, GenerationConfig
        from sqlalchemy import select

        try:
            config = kwargs.get("config", kwargs)
            async with async_session() as session:
                summary = await session.get(KnowledgeSummary, summary_id)
                if not summary:
                    return AgentResult(agent=self.name, status="error", error="Summary not found")
                if not await session.get(Document, document_id):
                    return AgentResult(agent=self.name, status="error", error="资料不存在")
                if document_id not in [summary.document_id] + (summary.document_ids or []):
                    return AgentResult(agent=self.name, status="error", error="知识总结与当前资料不匹配")

                # Load knowledge points
                stmt = select(KnowledgePointNode).where(
                    KnowledgePointNode.summary_id == summary_id
                ).order_by(KnowledgePointNode.level, KnowledgePointNode.sequence)
                result = await session.execute(stmt)
                nodes = result.scalars().all()

                if not nodes:
                    from app.core.knowledge.summary_generator import summary_generator
                    node_dicts = summary_generator.extract_nodes_from_markdown(summary.content_md, document_id)
                    knowledge_text = "\n".join(
                        f"{n['title']}: {n['explanation'][:300]}" for n in node_dicts
                    )
                else:
                    knowledge_text = "\n".join(
                        f"{n.title}: {n.explanation[:300]}" for n in nodes
                    )

                # Generate vocabulary via LLM
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是一位语言教学专家。请从以下知识内容中提取重要的生词/术语，"
                            "为每个词提供：单词（原词）、音标、词性、中文释义、英文例句。"
                            "输出严格JSON数组格式："
                            '[{"word":"...","phonetic":"...","part_of_speech":"...","definition":"...","example_sentence":"..."}]'
                            "\n只输出JSON，不要任何额外文字。限制30个词。"
                        ),
                    },
                    {"role": "user", "content": knowledge_text[:8000]},
                ]

                llm_result = await api_client.generate(
                    task_type=TaskType.KNOWLEDGE_TREE,
                    messages=messages,
                    prompt_template_id="language.vocabulary_extract",
                    generation_content=knowledge_text[:8000],
                    config=GenerationConfig(
                        model=config.get("model", "deepseek-chat"), temperature=0.3,
                        response_validator=lambda content: bool(self._parse_vocabulary(content)),
                    ),
                )

                # Parse vocabulary
                vocab_items = self._parse_vocabulary(llm_result.content)

                # Store vocabulary
                existing = (await session.execute(select(LanguageVocabulary).where(
                    LanguageVocabulary.document_id == document_id))).scalars().all()
                by_word = {v.word: v for v in existing}
                stored = []
                seen = set()
                for item in vocab_items:
                    word = item.get("word", "").strip()
                    if not word or word in seen:
                        continue
                    seen.add(word)
                    vocab = by_word.get(word)
                    if vocab is None:
                        vocab = LanguageVocabulary(
                            document_id=document_id,
                            word=word,
                            phonetic=item.get("phonetic", ""),
                            part_of_speech=item.get("part_of_speech", ""),
                            definition=item.get("definition", ""),
                            example_sentence=item.get("example_sentence", ""),
                            difficulty=self._estimate_difficulty({**item, "word": word}),
                            knowledge_point_id=None,
                        )
                        session.add(vocab)
                    stored.append(vocab)

                await session.flush()
                stored_words = [
                    {
                        "id": vocab.id,
                        "word": vocab.word,
                        "phonetic": vocab.phonetic,
                        "part_of_speech": vocab.part_of_speech,
                        "definition": vocab.definition,
                        "example_sentence": vocab.example_sentence,
                        "difficulty": vocab.difficulty,
                    } for vocab in stored
                ]

                await session.commit()

                return AgentResult(
                    agent=self.name,
                    status="success",
                    result={
                        "total_words": len(stored_words),
                        "words": stored_words,
                    },
                )

        except Exception as e:
            logger.error(f"LanguageAgent failed: {e}", exc_info=True)
            return AgentResult(agent=self.name, status="error", error=str(e))

    @staticmethod
    def _parse_vocabulary(content: str) -> list[dict]:
        """Use the same storage contract before caching and before writing words."""
        import json
        import re

        try:
            items = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if not match:
                raise ValueError("模型未返回有效的生词数组，请重试")
            items = json.loads(match.group(0))

        if not isinstance(items, list) or any(
            not isinstance(item, dict) or
            not isinstance(item.get("word"), str) or
            any(not isinstance(item.get(field, ""), str) for field in (
                "phonetic", "part_of_speech", "definition", "example_sentence"))
            for item in items
        ):
            raise ValueError("模型返回的生词格式无效，请重试")
        if any(
            len(item["word"].strip()) > 128 or
            len(item.get("phonetic", "")) > 64 or
            len(item.get("part_of_speech", "")) > 32
            for item in items
        ):
            raise ValueError("模型返回的生词字段过长，请重试")
        if not any(item["word"].strip() for item in items):
            raise ValueError("模型未返回可用的生词，请重试")
        return items

    def _estimate_difficulty(self, item: dict) -> str:
        """Estimate word difficulty based on length and type."""
        word = item.get("word", "")
        pos = item.get("part_of_speech", "")

        if len(word) >= 12:
            return "hard"
        if len(word) <= 4 and pos in ("noun", "verb", "adj"):
            return "easy"
        return "medium"
