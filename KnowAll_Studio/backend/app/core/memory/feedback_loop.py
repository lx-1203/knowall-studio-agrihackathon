"""Feedback loop engine - detects weak knowledge points and pushes to review queue"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select, func, and_, or_
from app.database import async_session
from app.models import (
    AnswerRecord, Flashcard, ReviewQueue, KnowledgeCoverage,
    ReviewSchedule, ReviewLog,
)

from app.core.card_access import visible_cards


def visible_resource(resource_type, resource_id, user_id):
    return or_(resource_type != 'flashcard', resource_id.in_(
        select(Flashcard.id).where(visible_cards(user_id))))

logger = logging.getLogger(__name__)

# Tiered push strategy
PUSH_STRATEGY = {
    "urgent": {"threshold": 0.4, "priority": 10, "action": "立即推送通知"},
    "review":  {"threshold": 0.7, "priority": 5,  "action": "加入复习队列"},
    "monitor": {"threshold": 0.85,"priority": 1,  "action": "仅记录监控"},
}


class FeedbackEngine:
    """Memory feedback loop: detect weak points and auto-push to review queue.

    Features:
    - Tiered accuracy thresholds with different push priorities
    - Decay detection: catch cards where ratings are trending down
    - Related card lookup: find sibling cards for context-aware review
    - Incremental scan: only process records since a given timestamp
    """

    DEFAULT_THRESHOLD = 0.7  # Accuracy below 70% triggers review

    async def scan(
        self,
        user_id: str = "local_user",
        threshold: float = DEFAULT_THRESHOLD,
        since: str | None = None,
    ) -> dict:
        """Scan all knowledge points and push weak ones to review queue.

        Args:
            user_id: User to scan for
            threshold: Accuracy threshold (default 0.7 = 70%)
            since: ISO datetime string — only scan records after this time

        Returns:
            Dict with scan results
        """
        total_scanned = 0
        weak_found = 0
        pushed_to_queue = 0

        async with async_session() as session:
            # Find all knowledge points with answer records
            ans_stmt = select(AnswerRecord).where(
                AnswerRecord.user_id == user_id,
                AnswerRecord.knowledge_point_ids != None,
            )
            if since:
                since_dt = datetime.fromisoformat(since)
                ans_stmt = ans_stmt.where(AnswerRecord.answered_at >= since_dt)

            ans_result = await session.execute(ans_stmt)
            answer_records = ans_result.scalars().all()

            # Aggregate by knowledge point
            kp_stats: dict[str, dict] = {}
            for record in answer_records:
                if not record.knowledge_point_ids:
                    continue
                for kp_id in record.knowledge_point_ids:
                    if kp_id not in kp_stats:
                        kp_stats[kp_id] = {"total": 0, "correct": 0}
                    kp_stats[kp_id]["total"] += 1
                    if record.is_correct:
                        kp_stats[kp_id]["correct"] += 1

            # Check flashcard accuracy too
            card_stmt = select(Flashcard).where(
                Flashcard.review_count > 0,
                visible_cards(user_id),
            )
            if since:
                card_stmt = card_stmt.where(Flashcard.last_review_at >= since_dt)
            card_result = await session.execute(card_stmt)
            cards = card_result.scalars().all()

            for card in cards:
                if card.knowledge_point_id:
                    if card.knowledge_point_id not in kp_stats:
                        kp_stats[card.knowledge_point_id] = {"total": 0, "correct": 0}
                    kp_stats[card.knowledge_point_id]["total"] += card.review_count
                    kp_stats[card.knowledge_point_id]["correct"] += card.correct_count

            total_scanned = len(kp_stats)

            # Pass 1 — decide which knowledge points are weak, without touching
            # the database. Looking up coverage and queue membership per point
            # made this O(N) queries (2 per weak point); both lookups are
            # batched in pass 2. Guarded by
            # tests/test_memory/test_feedback_scan_queries.py.
            weak_points: list[tuple[str, str]] = []  # (kp_id, tier)
            for kp_id, stats in kp_stats.items():
                if stats["total"] == 0:
                    continue

                accuracy = stats["correct"] / stats["total"]

                # Determine tier
                tier = None
                for tier_name, config in sorted(
                    PUSH_STRATEGY.items(),
                    key=lambda x: x[1]["threshold"],
                ):
                    if accuracy < config["threshold"]:
                        tier = tier_name
                        break

                if tier is None or accuracy >= threshold:
                    continue

                weak_found += 1
                weak_points.append((kp_id, tier))

            # Pass 2 — two queries cover every weak point, whatever N is.
            weak_ids = [kp_id for kp_id, _ in weak_points]
            coverages_by_kp: dict[str, list] = {}
            queued_kp_ids: set[str] = set()
            if weak_ids:
                cov_result = await session.execute(
                    select(KnowledgeCoverage).where(
                        KnowledgeCoverage.knowledge_point_id.in_(weak_ids),
                        visible_resource(KnowledgeCoverage.resource_type, KnowledgeCoverage.resource_id, user_id)
                    )
                )
                for cov in cov_result.scalars().all():
                    coverages_by_kp.setdefault(cov.knowledge_point_id, []).append(cov)

                existing_result = await session.execute(
                    select(ReviewQueue.knowledge_point_id).where(
                        and_(
                            ReviewQueue.user_id == user_id,
                    visible_resource(ReviewQueue.resource_type, ReviewQueue.resource_id, user_id),
                            ReviewQueue.knowledge_point_id.in_(weak_ids),
                            ReviewQueue.completed == False,
                        )
                    )
                )
                queued_kp_ids = {row[0] for row in existing_result.fetchall() if row[0]}

            for kp_id, tier in weak_points:
                if kp_id in queued_kp_ids:
                    continue

                priority = PUSH_STRATEGY[tier]["priority"]
                for cov in coverages_by_kp.get(kp_id, []):
                    queue_item = ReviewQueue(
                        user_id=user_id,
                        resource_type=cov.resource_type,
                        resource_id=cov.resource_id,
                        knowledge_point_id=kp_id,
                        priority=priority,
                        reason=tier,
                    )
                    session.add(queue_item)
                    pushed_to_queue += 1

            await session.commit()

        return {
            "total_scanned": total_scanned,
            "weak_found": weak_found,
            "pushed_to_queue": pushed_to_queue,
            "threshold": threshold,
            "since": since,
            "tier_distribution": {},  # populated below if needed
        }

    async def detect_decay(
        self,
        user_id: str = "local_user",
        lookback_reviews: int = 5,
        decay_threshold: int = 2,
    ) -> list[dict]:
        """Detect cards whose ratings are trending downward (memory decay).

        A card is in "decay" if among the last N reviews, at least
        `decay_threshold` of the most recent ratings are AGAIN or HARD.

        Args:
            user_id: User to scan
            lookback_reviews: Number of recent reviews to check
            decay_threshold: Minimum count of low ratings to trigger

        Returns:
            List of decaying card dicts
        """
        async with async_session() as session:
            # Get cards with enough review history
            card_stmt = select(Flashcard).where(
                Flashcard.review_count >= lookback_reviews,
                visible_cards(user_id),
            )
            card_result = await session.execute(card_stmt)
            cards = card_result.scalars().all()

            # Batch both per-card lookups. The loop form issued one query for the
            # card's logs and — for every decaying card — another for the queue
            # check, i.e. up to 2 per card. The log query is ordered in SQL
            # (card_id, review_at DESC) so the per-card top-N slice is exact.
            # Guarded by tests/test_memory/test_decay_queries.py.
            card_ids = [c.id for c in cards]
            recent_ratings: dict[str, list[int]] = {}
            if card_ids:
                log_rows = await session.execute(
                    select(ReviewLog.card_id, ReviewLog.rating)
                    .where(ReviewLog.card_id.in_(card_ids))
                    .order_by(ReviewLog.card_id, ReviewLog.review_at.desc())
                )
                for card_id, rating in log_rows.fetchall():
                    bucket = recent_ratings.setdefault(card_id, [])
                    if len(bucket) < lookback_reviews:
                        bucket.append(rating)

            candidates = []
            for card in cards:
                ratings = recent_ratings.get(card.id, [])
                if len(ratings) < lookback_reviews:
                    continue
                # Count low ratings in the most recent reviews
                low_count = sum(1 for r in ratings if r in (1, 2))  # AGAIN or HARD
                if low_count >= decay_threshold:
                    candidates.append((card, ratings, low_count))

            queued_ids: set[str] = set()
            if candidates:
                existing_result = await session.execute(
                    select(ReviewQueue.resource_id).where(
                        and_(
                            ReviewQueue.user_id == user_id,
                    visible_resource(ReviewQueue.resource_type, ReviewQueue.resource_id, user_id),
                            ReviewQueue.resource_id.in_([c.id for c, _, _ in candidates]),
                            ReviewQueue.resource_type == "flashcard",
                            ReviewQueue.completed == False,
                            ReviewQueue.reason == "decay",
                        )
                    )
                )
                queued_ids = {row[0] for row in existing_result.fetchall()}

            decaying = []
            for card, ratings, low_count in candidates:
                if card.id in queued_ids:
                    continue

                # Push to queue with high priority
                queue_item = ReviewQueue(
                    user_id=user_id,
                    resource_type="flashcard",
                    resource_id=card.id,
                    knowledge_point_id=card.knowledge_point_id,
                    priority=9,  # High priority for decay
                    reason="decay",
                )
                session.add(queue_item)

                decaying.append({
                    "card_id": card.id,
                    "front": card.front[:80],
                    "knowledge_point_id": card.knowledge_point_id,
                    "recent_ratings": ratings,
                    "low_count": low_count,
                })

            await session.commit()
            return decaying

    async def get_related_cards(
        self,
        card_id: str,
        limit: int = 5,
        user_id: str = "local_user",
    ) -> list[dict]:
        """Find cards related to the given card (same knowledge point or similar tags)."""
        async with async_session() as session:
            card = await session.get(Flashcard, card_id)
            if not card:
                return []
            allowed = (await session.execute(select(Flashcard.id).where(
                Flashcard.id == card_id, visible_cards(user_id)))).scalar_one_or_none()
            if allowed is None:
                return []

            related = []

            # Strategy 1: Same knowledge_point_id
            if card.knowledge_point_id:
                kp_stmt = (
                    select(Flashcard)
                    .where(
                        and_(
                            Flashcard.knowledge_point_id == card.knowledge_point_id,
                            Flashcard.id != card_id,
                            visible_cards(user_id),
                        )
                    )
                    .limit(limit)
                )
                kp_result = await session.execute(kp_stmt)
                related.extend(kp_result.scalars().all())

            # Strategy 2: Same deck
            if len(related) < limit and card.deck_id:
                remaining = limit - len(related)
                existing_ids = {r.id for r in related}
                deck_stmt = (
                    select(Flashcard)
                    .where(
                        and_(
                            Flashcard.deck_id == card.deck_id,
                            Flashcard.id != card_id,
                            visible_cards(user_id),
                            Flashcard.id.notin_(existing_ids),
                        )
                    )
                    .limit(remaining)
                )
                deck_result = await session.execute(deck_stmt)
                related.extend(deck_result.scalars().all())

            return [
                {
                    "id": c.id,
                    "card_type": c.card_type,
                    "front": c.front[:100],
                    "back": c.back[:100],
                    "knowledge_point_id": c.knowledge_point_id,
                    "relation": "same_kp" if c.knowledge_point_id == card.knowledge_point_id else "same_deck",
                }
                for c in related[:limit]
            ]

    async def get_review_queue(
        self,
        user_id: str = "local_user",
        limit: int = 20,
    ) -> list[dict]:
        """Get the review queue for a user, sorted by priority."""
        async with async_session() as session:
            stmt = select(ReviewQueue).where(
                and_(
                    ReviewQueue.user_id == user_id,
                    visible_resource(ReviewQueue.resource_type, ReviewQueue.resource_id, user_id),
                    ReviewQueue.completed == False,
                )
            ).order_by(ReviewQueue.priority.desc()).limit(limit)
            result = await session.execute(stmt)
            items = result.scalars().all()

            return [
                {
                    "queue_id": item.id,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "knowledge_point_id": item.knowledge_point_id,
                    "priority": item.priority,
                    "reason": item.reason,
                    "pushed_at": item.pushed_at.isoformat() if item.pushed_at else None,
                    "completed": item.completed,
                }
                for item in items
            ]

    async def mark_completed(self, queue_id: str) -> bool:
        """Mark a review queue item as completed."""
        async with async_session() as session:
            item = await session.get(ReviewQueue, queue_id)
            if not item:
                return False
            item.completed = True
            item.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()
            return True

    async def record_answer_result(
        self,
        card_id: str,
        is_correct: bool,
    ) -> None:
        """Update flashcard accuracy stats after a review."""
        async with async_session() as session:
            card = await session.get(Flashcard, card_id)
            if not card:
                return

            card.review_count = (card.review_count or 0) + 1
            if is_correct:
                card.correct_count = (card.correct_count or 0) + 1

            if card.review_count > 0:
                card.accuracy_rate = card.correct_count / card.review_count

            card.last_review_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()

    async def get_memory_stats(self, user_id: str = "local_user") -> dict:
        """Get memory/learning statistics for a user."""
        async with async_session() as session:
            # Total flashcards
            total_stmt = select(func.count()).select_from(Flashcard).where(visible_cards(user_id))
            total_result = await session.execute(total_stmt)
            total_cards = total_result.scalar() or 0

            # Total reviews
            review_stmt = select(func.sum(Flashcard.review_count)).select_from(Flashcard).where(visible_cards(user_id))
            review_result = await session.execute(review_stmt)
            total_reviews = review_result.scalar() or 0

            # Average accuracy
            acc_stmt = select(func.avg(Flashcard.accuracy_rate)).where(
                Flashcard.review_count > 0,
                visible_cards(user_id),
            )
            acc_result = await session.execute(acc_stmt)
            avg_accuracy = acc_result.scalar() or 0.0

            # Weak points by tier
            tier_counts = {"urgent": 0, "review": 0, "monitor": 0}
            card_stmt = select(Flashcard).where(Flashcard.review_count > 0, visible_cards(user_id))
            card_result = await session.execute(card_stmt)
            for card in card_result.scalars().all():
                acc = card.accuracy_rate or 0.0
                if acc < 0.4:
                    tier_counts["urgent"] += 1
                elif acc < 0.7:
                    tier_counts["review"] += 1
                elif acc < 0.85:
                    tier_counts["monitor"] += 1

            weak_count = tier_counts["urgent"] + tier_counts["review"]

            # Due today
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            due_stmt = select(func.count()).select_from(ReviewSchedule).join(Flashcard, ReviewSchedule.card_id == Flashcard.id).where(
                visible_cards(user_id),
                ReviewSchedule.next_review_at <= now,
            )
            due_result = await session.execute(due_stmt)
            due_today = due_result.scalar() or 0

            # Queue count
            queue_stmt = select(func.count()).select_from(ReviewQueue).where(
                and_(
                    ReviewQueue.user_id == user_id,
                    visible_resource(ReviewQueue.resource_type, ReviewQueue.resource_id, user_id),
                    ReviewQueue.completed == False,
                )
            )
            queue_result = await session.execute(queue_stmt)
            queue_count = queue_result.scalar() or 0

            # Decaying cards
            decay_stmt = select(func.count()).select_from(ReviewQueue).where(
                and_(
                    ReviewQueue.user_id == user_id,
                    visible_resource(ReviewQueue.resource_type, ReviewQueue.resource_id, user_id),
                    ReviewQueue.completed == False,
                    ReviewQueue.reason == "decay",
                )
            )
            decay_result = await session.execute(decay_stmt)
            decay_count = decay_result.scalar() or 0

            return {
                "total_cards": total_cards,
                "total_reviews": total_reviews,
                "average_accuracy": round(float(avg_accuracy), 4),
                "weak_points_count": weak_count,
                "due_today": due_today,
                "review_queue_count": queue_count,
                "decay_count": decay_count,
                "tier_distribution": tier_counts,
            }


feedback_engine = FeedbackEngine()
