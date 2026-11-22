"""Validate optional deterministic quiz rubrics without discarding extensions."""
from pydantic import BaseModel, ConfigDict, Field


class Blank(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    index: int = Field(ge=0)
    answer: str
    hint: str | None = None


class ScoringPoint(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    point: str
    keywords: list[str]
    score: float = Field(ge=0, allow_inf_nan=False)


class Rubrics(BaseModel):
    model_config = ConfigDict(strict=True)
    blanks: list[Blank] = Field(default_factory=list)
    scoring_points: list[ScoringPoint] = Field(default_factory=list)


def question_rubrics(question: dict) -> dict:
    # Missing/NULL means a legacy question; falsey malformed containers must
    # still be validated rather than silently treated as absent.
    data = {key: [] if question.get(key) is None else question[key]
            for key in ('blanks', 'scoring_points')}
    return Rubrics.model_validate(data).model_dump(exclude_none=True)
