"""Study Plan Agent - generates short-term and long-term study plans with Ebbinghaus review nodes"""
import logging
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from app.core.agents.base import BaseAgent, AgentRegistry, AgentResult

logger = logging.getLogger(__name__)

# Ebbinghaus forgetting curve: review at day 1, 2, 4, 7, 15
EBBINGHAUS_INTERVALS = [1, 2, 4, 7, 15]


class StudyPlanOptions(BaseModel):
    plan_type: Literal['short', 'long', 'both'] = 'both'
    # Slots start at 08:00; a daily budget must fit before midnight.
    daily_hours: float = Field(default=2.0, ge=1 / 60, le=16, strict=True, allow_inf_nan=False)
    start_date: str | None = None
    ebbinghaus_enabled: bool = True

    @field_validator('start_date')
    @classmethod
    def valid_start_date(cls, value):
        if value is not None:
            datetime.fromisoformat(value)
        return value


@AgentRegistry.register("study_plan")
class StudyPlanAgent(BaseAgent):
    """Generates study plans with Ebbinghaus spaced repetition nodes."""

    name = "study_plan"
    description = "基于知识点总结生成学习计划，结合艾宾浩斯遗忘曲线安排复习"

    async def run(self, summary_id: str, document_id: str, **kwargs) -> AgentResult:
        from app.database import async_session
        from app.models import KnowledgeSummary, KnowledgePointNode, StudyPlan
        from sqlalchemy import select

        try:
            # The API passes config=..., while the orchestrator expands **config.
            config = kwargs.get('config', kwargs)
            study_config = config.get('study_plan', {})
            options = StudyPlanOptions(
                plan_type=study_config.get('type', 'both'),
                daily_hours=study_config.get('daily_hours', 2.0),
                start_date=config.get('start_date'),
                ebbinghaus_enabled=study_config.get('ebbinghaus_enabled', True),
            )
            plan_type, daily_hours = options.plan_type, options.daily_hours
            async with async_session() as session:
                summary = await session.get(KnowledgeSummary, summary_id)
                if not summary:
                    return AgentResult(agent=self.name, status="error", error="Summary not found")

                # Load nodes
                stmt = select(KnowledgePointNode).where(
                    KnowledgePointNode.summary_id == summary_id
                ).order_by(KnowledgePointNode.level, KnowledgePointNode.sequence)
                result = await session.execute(stmt)
                nodes = result.scalars().all()

                if not nodes:
                    from app.core.knowledge.summary_generator import summary_generator
                    node_dicts = summary_generator.extract_nodes_from_markdown(summary.content_md, document_id)
                    nodes = node_dicts

                # Determine start date
                if options.start_date:
                    start_date = datetime.fromisoformat(options.start_date)
                    if start_date.tzinfo:
                        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    start_date = datetime.now() + timedelta(days=1)

                # Collect L1 topics as planning units
                l1_nodes = [n for n in nodes if (n.level if hasattr(n, 'level') else n.get('level')) == 1]
                l2_l3_nodes = [n for n in nodes if (n.level if hasattr(n, 'level') else n.get('level')) >= 2]

                total_topics = len(l2_l3_nodes) if l2_l3_nodes else len(l1_nodes)
                if not total_topics:
                    return AgentResult(agent=self.name, status='error', error='知识点为空，不能生成学习计划')
                topic_nodes = l2_l3_nodes or l1_nodes
                topic_ids = [n.id if hasattr(n, 'id') else n['id'] for n in topic_nodes]

                # Build Ebbinghaus review nodes
                ebbinghaus_nodes = []
                for day in EBBINGHAUS_INTERVALS if options.ebbinghaus_enabled else []:
                    ebbinghaus_nodes.append({
                        "day": day,
                        "review": True,
                        "description": f"第{day}天复习 - 根据艾宾浩斯遗忘曲线，此时记忆留存率约为{self._retention_rate(day)}%",
                    })

                short_term_plan = None
                long_term_plan = None

                if plan_type in ("short", "both"):
                    short_term_plan = self._build_short_term_plan(
                        total_topics, daily_hours, start_date, topic_ids
                    )

                if plan_type in ("long", "both"):
                    long_term_plan = self._build_long_term_plan(
                        total_topics, daily_hours, start_date, l1_nodes, options.ebbinghaus_enabled
                    )

                # Persist the exact generated schedule; later reads must not
                # depend on current knowledge nodes or regenerate a new plan.
                dates = [day['date'] for day in short_term_plan or []]
                dates.extend(day['date'] for week in long_term_plan or [] for day in week['days'])
                # Inclusive calendar deadline, matching the last scheduled day.
                target_end_date = datetime.fromisoformat(max(dates))
                plan = StudyPlan(
                    user_id=kwargs.get('user_id', config.get('user_id', 'local_user')),
                    name=f"{summary.title if hasattr(summary, 'title') else '学习计划'} - {start_date.strftime('%Y-%m-%d')}",
                    description=f"基于 {total_topics} 个知识点的{'短期+长期' if plan_type == 'both' else plan_type + '期'}学习计划",
                    plan_type=plan_type,
                    daily_hours=daily_hours,
                    knowledge_point_ids=[n.id if hasattr(n, 'id') else n.get('id', '') for n in nodes],
                    ebbinghaus_nodes=ebbinghaus_nodes,
                    short_term_plan=short_term_plan,
                    long_term_plan=long_term_plan,
                    target_end_date=target_end_date,
                )
                session.add(plan)
                await session.commit()
                await session.refresh(plan)

                return AgentResult(
                    agent=self.name,
                    status="success",
                    result={
                        "plan_id": plan.id,
                        "name": plan.name,
                        "plan_type": plan_type,
                        "daily_hours": daily_hours,
                        "short_term_plan": short_term_plan,
                        "long_term_plan": long_term_plan,
                        "ebbinghaus_nodes": ebbinghaus_nodes,
                        "knowledge_point_ids": plan.knowledge_point_ids,
                    },
                )

        except Exception as e:
            logger.error(f"StudyPlanAgent failed: {e}", exc_info=True)
            return AgentResult(agent=self.name, status="error", error=str(e))

    def _build_short_term_plan(self, total_topics: int, daily_hours: float, start_date, topic_ids: list[str] | None = None) -> list[dict]:
        """Build 1-3 day hourly schedule."""
        plan = []
        days = min(3, max(1, total_topics // 15))
        minutes_per_day = round(daily_hours * 60)
        slots_per_day = ceil(minutes_per_day / 60)
        learning_slots = min(total_topics, days * slots_per_day)
        next_topic = 1
        slot_index = 0

        for day_idx in range(days):
            date = start_date + timedelta(days=day_idx)
            hours = []
            for hour_idx in range(slots_per_day):
                start_minute = 8 * 60 + hour_idx * 60
                end_minute = 8 * 60 + min((hour_idx + 1) * 60, minutes_per_day)
                hour_str = f'{start_minute // 60:02d}:{start_minute % 60:02d}-{end_minute // 60:02d}:{end_minute % 60:02d}'
                if slot_index < learning_slots:
                    count = ceil((total_topics - next_topic + 1) / (learning_slots - slot_index))
                    last_topic = next_topic + count - 1
                    hours.append({
                        "hour": hour_str,
                        "topic": f'学习知识点 {next_topic}-{last_topic}',
                        "knowledge_point_ids": (topic_ids or [])[next_topic - 1:last_topic],
                        "is_review": False,
                    })
                    next_topic = last_topic + 1
                else:
                    hours.append({
                        "hour": hour_str,
                        "topic": "自由复习/休息",
                        "knowledge_point_ids": [],
                        "is_review": True,
                    })
                slot_index += 1
            plan.append({"day": day_idx + 1, "date": date.strftime("%Y-%m-%d"), "hours": hours})

        return plan

    def _build_long_term_plan(self, total_topics: int, daily_hours: float, start_date, l1_nodes: list, ebbinghaus_enabled: bool = True) -> list[dict]:
        """Build 1-4 week daily schedule with Ebbinghaus review days."""
        plan = []
        weeks = min(4, max(1, total_topics // 5))
        topics_per_day = max(2, ceil(total_topics / (weeks * 7)))
        next_topic = 1

        for week_idx in range(weeks):
            days = []
            for day_idx in range(7):
                date = start_date + timedelta(days=week_idx * 7 + day_idx)
                day_num = week_idx * 7 + day_idx + 1

                # Check if this day is an Ebbinghaus review day
                is_review = ebbinghaus_enabled and day_num in EBBINGHAUS_INTERVALS
                ebbinghaus_day = day_num if is_review else None

                last_topic = min(total_topics + 1, next_topic + topics_per_day)

                days.append({
                    "day": day_idx + 1,
                    "date": date.strftime("%Y-%m-%d"),
                    "topics": [f'知识点 {i}' for i in range(next_topic, last_topic)],
                    "hours": daily_hours,
                    "is_review_day": is_review,
                    "ebbinghaus_day": ebbinghaus_day,
                })
                next_topic = last_topic

            plan.append({"week": week_idx + 1, "days": days})

        return plan

    def _retention_rate(self, day: int) -> int:
        """Estimate memory retention rate based on Ebbinghaus curve."""
        rates = {1: 58, 2: 44, 4: 36, 7: 28, 15: 21, 30: 20}
        return rates.get(day, 20)
