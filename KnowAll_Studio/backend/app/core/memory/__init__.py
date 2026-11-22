"""Flashcard generation + FSRS spaced repetition scheduler (M4)"""
import json
import math
import re
import logging
from datetime import datetime, timedelta, timezone
from app.core.api_scheduler import api_client, TaskType, GenerationConfig

logger = logging.getLogger(__name__)


class FlashcardGenerator:
    """Generate flashcard content via API with quality validation."""

    # ── Validation constants ────────────────────────────────────
    MIN_FRONT_LENGTH = 4       # Minimum characters for card front
    MIN_BACK_LENGTH = 2        # Minimum characters for card back
    CLOZE_PATTERN = re.compile(r'\{\{c\d+::[^}]+?\}\}')  # {{c1::answer}}

    async def generate_cards(
        self,
        knowledge_text: str,
        card_type: str = "qa",
        count: int = 20,
        deck_name: str = "默认牌组",
        model: str = "deepseek-chat",
        knowledge_point_id: str = None,
    ) -> list[dict]:
        """Generate flashcards with optional knowledge point linking.

        Extended interface used by agents for coverage-mapped generation.
        """
        cards = await self.generate(knowledge_text, card_type, count, model)

        # Tag each card with knowledge_point_id if provided
        if knowledge_point_id:
            for card in cards:
                card["knowledge_point_id"] = knowledge_point_id

        return cards

    async def generate(
        self,
        knowledge_text: str,
        card_type: str = "qa",
        count: int = 20,
        model: str = "deepseek-chat",
    ) -> list[dict]:
        """Generate flashcards from knowledge text with quality validation."""
        from app.prompts import prompt_engine

        # Preprocess: truncate overly long knowledge text
        knowledge_text = self._preprocess_text(knowledge_text)

        messages = prompt_engine.render(
            "flashcard", card_type,
            knowledge_points=knowledge_text,
            count=count,
        )

        result = await api_client.generate(
            task_type=TaskType.FLASHCARD_GEN,
            messages=messages,
            prompt_template_id=f"flashcard.{card_type}",
            generation_content=knowledge_text + card_type + str(count),
            config=GenerationConfig(model=model, response_validator=lambda content:
                                    bool(self._validated_cards(content, card_type))),
        )
        return self._validated_cards(result.content, card_type)

    def _validated_cards(self, content: str, requested_type: str) -> list[dict]:
        """Share usable-card selection between cache validation and generation."""
        cards = self._parse_cards(content)
        valid_cards = []
        for c in cards:
            if not isinstance(c, dict):
                continue
            c = dict(c)
            if c.get("card_type") is None:
                c["card_type"] = requested_type
            is_valid, reason = self.validate_card(c, c["card_type"])
            if is_valid:
                if c.get("tags") is None:
                    c["tags"] = []
                if c.get("hints") is None:
                    c["hints"] = ""
                valid_cards.append(c)
            else:
                logger.warning("Card filtered: type=%s reason=%s", requested_type, reason)

        return valid_cards

    # ── Quality validation (public static) ──────────────────────

    @staticmethod
    def validate_card(card: dict, card_type: str) -> tuple[bool, str]:
        """Validate card structure. Returns (is_valid, error_reason)."""
        if not isinstance(card, dict):
            return False, "invalid_card_structure"
        if card_type not in ("qa", "cloze", "compare"):
            return False, "invalid_card_type"
        if not isinstance(card.get("front"), str) or not isinstance(card.get("back"), str):
            return False, "invalid_front_or_back_type"
        tags = card.get("tags")
        if tags is not None:
            lists = list(tags.values()) if isinstance(tags, dict) else [tags]
            if not all(isinstance(items, list) and all(isinstance(t, str) for t in items) for items in lists):
                return False, "invalid_tags"
        if card.get("hints") is not None and not isinstance(card["hints"], str):
            return False, "invalid_hints"
        front = card["front"].strip()
        back = card["back"].strip()

        # Both sides must have content
        if not front:
            return False, "empty_front"
        if not back:
            return False, "empty_back"

        # Front too short (except for formula-type cards)
        if len(front) < FlashcardGenerator.MIN_FRONT_LENGTH:
            return False, f"front_too_short({len(front)})"

        # Cloze-specific: must have at least one cloze marker
        if card_type == "cloze" and not FlashcardGenerator.CLOZE_PATTERN.search(front):
            return False, "missing_cloze_marker"

        # QA: front and back should not be identical
        if card_type == "qa" and front == back:
            return False, "front_equals_back"

        return True, ""

    # ── JSON parsing with repair ─────────────────────────────────

    def _parse_cards(self, content: str) -> list[dict]:
        """Parse LLM output to card list with multi-layer JSON repair."""
        if not content or not content.strip():
            return []

        # Layer 1: Direct JSON parse
        try:
            data = json.loads(content)
            return self._extract_cards(data)
        except json.JSONDecodeError:
            pass

        # Layer 2: Extract from ```json ... ``` code block
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if m:
            try:
                data = json.loads(m.group(1))
                return self._extract_cards(data)
            except json.JSONDecodeError:
                pass

        # Layer 3: Find outermost JSON object or array in the raw text
        return self._repair_and_parse(content)

    def _extract_cards(self, data) -> list[dict]:
        """Normalize parsed data into card list."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            cards = data.get("cards", [])
            return cards if isinstance(cards, list) else []
        return []

    def _repair_and_parse(self, content: str) -> list[dict]:
        """Last-resort: try to salvage individual card objects from broken JSON.

        Strategy: find each {...} block that looks like a card and parse it individually.
        """
        cards = []
        # Find all balanced { ... } blocks that contain "front" key
        for m in re.finditer(r'\{[^{}]*?"front"\s*:\s*"[^"]*"[^{}]*?"back"\s*:\s*"[^"]*"[^{}]*?\}', content):
            try:
                card = json.loads(m.group())
                if "front" in card and "back" in card:
                    cards.append(card)
            except json.JSONDecodeError:
                continue

        if cards:
            logger.info(f"Repaired {len(cards)} cards from broken JSON")
        return cards

    # ── Text preprocessing ──────────────────────────────────────

    @staticmethod
    def _preprocess_text(text: str, max_chars: int = 6000) -> str:
        """Truncate overly long knowledge text to prevent LLM context dilution."""
        if len(text) <= max_chars:
            return text
        # Keep first 60% and last 20% (the middle is often repetitive)
        head = int(max_chars * 0.7)
        tail = int(max_chars * 0.2)
        truncated = text[:head] + "\n\n...(中间内容省略)...\n\n" + text[-tail:]
        logger.info(f"Text truncated from {len(text)} to {len(truncated)} chars")
        return truncated


class FSRS:
    """Dictionary adapter for MIT py-fsrs 6.3.2, using UTC throughout.

    Single learning/relearning steps allow reconstruction with step=0,
    without an extra database step field. All scheduling math is upstream.
    """
    from fsrs import Scheduler as _Scheduler
    DEFAULT_W = list(_Scheduler(enable_fuzzing=False).parameters)
    VERSION = "fsrs-6"
    AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
    MAX_DUE_PER_DAY = 50

    def __init__(self, w: list[float] | None = None):
        self.w = list(self.DEFAULT_W if w is None else w)
        if len(self.w) != 21:
            raise ValueError("FSRS 6 requires 21 parameters; legacy 17 parameters cannot be reused")
        self._scheduler()
        self._user_w_cache: dict[str, list[float]] = {}

    def _scheduler(self):
        return self._Scheduler(parameters=self.w, learning_steps=(timedelta(minutes=1),),
                               relearning_steps=(timedelta(minutes=10),), enable_fuzzing=False)

    def init_card(self) -> dict:
        return dict(stability=0.0, difficulty=0.0, retrievability=0.0,
                    next_review_at=None, last_review_at=None, review_count=0,
                    state="new", fsrs_version=self.VERSION)

    @staticmethod
    def _utc(value):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def review(self, card_state: dict, rating: int) -> dict:
        from fsrs import Card, Rating, State
        if type(rating) is not int or rating not in (1, 2, 3, 4):
            raise ValueError("rating must be an integer from 1 to 4")
        state = dict(card_state)
        now = datetime.now(timezone.utc)
        stability = float(state.get("stability") or 0)
        difficulty = float(state.get("difficulty") or 0)
        last_review = self._utc(state.get("last_review_at"))
        if state.get("fsrs_version") not in (None, "", self.VERSION):
            raise ValueError("Unsupported FSRS state version")
        if not state.get("fsrs_version") and math.isfinite(difficulty) and 0 <= difficulty <= 1:
            difficulty = 1 + 9 * difficulty
        valid = (math.isfinite(stability) and stability > 0 and math.isfinite(difficulty)
                 and difficulty > 0 and last_review is not None)
        if state.get("state") == "new" or not valid:
            # Recalibrate damaged memory estimates; preserve count and history.
            card = Card(card_id=0, due=now)
        else:
            kind = {"learning": State.Learning, "review": State.Review,
                    "relearning": State.Relearning}.get(state.get("state"))
            if kind is None:
                raise ValueError("Unsupported FSRS card state")
            card = Card(card_id=0, state=kind, step=0 if kind != State.Review else None,
                        stability=stability, difficulty=max(1.0, min(10.0, difficulty)),
                        last_review=min(last_review, now), due=self._utc(state.get("next_review_at")) or now)
        scheduler = self._scheduler()
        retrievability = scheduler.get_card_retrievability(card, current_datetime=now)
        updated, _ = scheduler.review_card(card, Rating(rating), review_datetime=now)
        state.update(stability=updated.stability, difficulty=updated.difficulty,
                     retrievability=retrievability, fsrs_version=self.VERSION,
                     next_review_at=updated.due.replace(tzinfo=None),
                     last_review_at=updated.last_review.replace(tzinfo=None),
                     review_count=int(state.get("review_count") or 0) + 1,
                     state={State.Learning: "learning", State.Review: "review",
                            State.Relearning: "relearning"}[updated.state])
        return state

    def get_due_cards(self, cards: list[dict]) -> list[dict]:
        """Filter cards that are due for review.

        Note: Prefer the SQL-level filtering in the API route for large datasets.
        This method is retained for in-memory use on small lists.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        due = []
        for card in cards:
            schedule = card.get("schedule", {})
            if schedule.get("state") == "new":
                due.append(card)
            elif schedule.get("next_review_at"):
                if schedule["next_review_at"] <= now:
                    due.append(card)
        return due

    def balance_load(
        self,
        due_cards: list,
        max_per_day: int | None = None,
    ) -> list:
        """Apply load balancing: if too many cards are due, spread some to next day.

        Returns the same list, but modifies next_review_at for overflow cards.
        """
        if max_per_day is None:
            max_per_day = self.MAX_DUE_PER_DAY

        if len(due_cards) <= max_per_day:
            return due_cards

        tomorrow = datetime.now(timezone.utc).replace(
            tzinfo=None, hour=8, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)

        overflow = due_cards[max_per_day:]
        for card in overflow:
            schedule = card.get("schedule", {})
            schedule["next_review_at"] = tomorrow
            schedule["state"] = "review"

        logger.info(
            f"Load balanced: {len(overflow)} cards deferred to {tomorrow.date()}"
        )
        return due_cards

    # ── Personalized weight optimization ────────────────────────

    def optimize_weights(self, ratings_history: list[dict], epochs: int = 50,
                         lr: float = 0.01, user_id: str | None = None) -> list[float]:
        """Retain weights: summary statistics cannot train standard FSRS.

        The official optimizer requires dated, card-linked ReviewLogs.
        Do not pretend a personalized fit occurred with incomplete history.
        """
        logger.warning("FSRS optimization not performed: complete ReviewLogs required")
        return self.get_user_weights(user_id) if user_id else list(self.w)

    def get_user_weights(self, user_id: str) -> list[float]:
        """Get cached personalized weights for a user."""
        return self._user_w_cache.get(user_id, list(self.w))

    def load_user_weights(self, user_id: str) -> list[float]:
        """Load personalized weights for use in this FSRS instance."""
        cached = self.get_user_weights(user_id)
        self.w = list(cached)
        self._scheduler()
        return list(cached)


card_generator = FlashcardGenerator()
fsrs = FSRS()
