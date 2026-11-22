"""Book data models for the Book Engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BookStatus(str, Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    COMPILING = "compiling"
    COMPLETED = "completed"
    ERROR = "error"


class BlockType(str, Enum):
    TEXT = "text"
    QUIZ = "quiz"
    CALLOUT = "callout"
    CODE = "code"
    FLASH_CARDS = "flash_cards"
    CONCEPT_GRAPH = "concept_graph"
    RETRIEVAL_PRACTICE = "retrieval_practice"
    DIAGNOSTIC = "diagnostic"


@dataclass
class BookBlock:
    """A single content block within a section."""
    id: str
    block_type: BlockType
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    order_index: int = 0


@dataclass
class BookSection:
    """A section within a chapter, containing multiple blocks."""
    id: str
    title: str
    description: str = ""
    blocks: list[BookBlock] = field(default_factory=list)
    order_index: int = 0


@dataclass
class BookChapter:
    """A chapter containing multiple sections."""
    id: str
    title: str
    description: str = ""
    sections: list[BookSection] = field(default_factory=list)
    order_index: int = 0


@dataclass
class BookProposal:
    """Proposal generated during ideation phase."""
    title: str
    subtitle: str = ""
    description: str = ""
    chapters: list[dict] = field(default_factory=list)  # [{title, description, sections: [...]}]
    target_audience: str = "大学生"
    estimated_pages: int = 0


@dataclass
class Book:
    """The complete book model."""
    id: str
    title: str
    subtitle: str = ""
    description: str = ""
    status: BookStatus = BookStatus.DRAFT
    source_kb: str = ""
    source_doc_ids: list[str] = field(default_factory=list)
    chapters: list[BookChapter] = field(default_factory=list)
    progress: float = 0.0  # 0-1
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    user_id: str = "local_user"
