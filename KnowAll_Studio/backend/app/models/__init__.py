"""All database models for KnowAll Studio"""
import uuid
import json
from datetime import datetime, date, timezone
from sqlalchemy import Column, String, Integer, BigInteger, Boolean, Float, Date, DateTime, Text, ForeignKey, JSON, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import relationship
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---- Auth & Knowledge Base ----
from app.models.user import User, KnowledgePoint  # noqa: E402, F401
from app.models.notification import Notification  # noqa: E402, F401
from app.models.user_history import UserHistory  # noqa: E402, F401
from app.models.user_bind import UserBind  # noqa: E402, F401
from app.models.kaf import KafFile, KafGrant  # noqa: E402, F401
from app.models.study_pack import StudyPack  # noqa: E402, F401
from app.models.knowledge_collection import KnowledgeCollection  # noqa: E402, F401
from app.models.agent_run import AgentRun  # noqa: E402, F401
from app.models.agent_background_task import AgentBackgroundTask  # noqa: E402, F401
from app.models.agent_input_checkpoint import AgentInputCheckpoint  # noqa: E402, F401


# ==================== Document Models ====================

class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    filename = Column(String(500), nullable=False)
    file_type = Column(String(50), nullable=False)  # pdf/docx/pptx/md/image/xmind/url/text
    file_size = Column(Integer)
    sha256 = Column(String(64), unique=True, nullable=False)
    local_path = Column(String(500))
    status = Column(String(50), default="pending")  # pending/parsing/ready/error
    page_count = Column(Integer)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    doc_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    # Identical text can appear in multiple documents (or twice in one document).
    # The hash describes content; it is not a row identity.
    content_hash = Column(String(64))  # SHA256 of normalized text
    text_content = Column(Text, nullable=False)
    token_count = Column(Integer)
    page_range = Column(String(20))  # "p3-p5"
    vector_id = Column(String(64))  # ChromaDB reference
    created_at = Column(DateTime, default=now)

    document = relationship("Document", back_populates="chunks")


# ==================== Knowledge Structure Models ====================

class KnowledgeTree(Base):
    __tablename__ = "knowledge_trees"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(200), nullable=False)
    doc_ids = Column(JSON, default=list)
    tree_data = Column(JSON, nullable=False, default=dict)
    generation_cache_key = Column(String(128))
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class Outline(Base):
    __tablename__ = "outlines"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    title = Column(String(200), nullable=False)
    content_markdown = Column(Text, nullable=False)
    source_tree_id = Column(String(36), ForeignKey("knowledge_trees.id"))
    generation_cache_key = Column(String(128))
    created_at = Column(DateTime, default=now)


# ==================== Quiz System Models ====================

class QuestionBank(Base):
    __tablename__ = "question_bank"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    question_type = Column(String(50), nullable=False)  # single_choice/multi_choice/true_false/fill_blank/cloze/short_answer/calculation/formula/coding/material_analysis/term_definition
    difficulty = Column(String(20), default="medium")  # legacy: easy/medium/hard
    difficulty_score = Column(Float, default=0.5)  # NEW: continuous 0.0-1.0
    cognitive_level = Column(String(20), default="L2_understand")  # NEW: Bloom level (L1-L6)
    tags = Column(JSON, default=list)
    question_text = Column(Text, nullable=False)
    options = Column(JSON, default=list)  # [{"label":"A","text":"..."}]
    correct_answer = Column(Text, nullable=False)
    blanks = Column(JSON, nullable=True, default=list)
    scoring_points = Column(JSON, nullable=True, default=list)
    analysis = Column(Text)
    review_scores = Column(JSON, default=dict)  # NEW: LLM-as-Judge quality scores
    review_total = Column(Float)  # NEW: total review score
    source_chunk_id = Column(String(36), ForeignKey("document_chunks.id"))
    parent_question_id = Column(String(36))  # variant question origin
    generation_cache_key = Column(String(128))
    created_at = Column(DateTime, default=now)


class ExamPaper(Base):
    __tablename__ = "exam_papers"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    question_ids = Column(JSON, nullable=False, default=list)
    config = Column(JSON, default=dict)  # time_limit, total_score, difficulty distribution
    created_at = Column(DateTime, default=now)


class AnswerRecord(Base):
    __tablename__ = "answer_records"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user")
    question_id = Column(String(36), ForeignKey("question_bank.id"), nullable=False)
    paper_id = Column(String(36), ForeignKey("exam_papers.id"))
    user_answer = Column(Text)
    is_correct = Column(Boolean)
    time_spent = Column(Integer)  # seconds (legacy)
    time_spent_ms = Column(Integer, default=0)  # milliseconds
    attempt_count = Column(Integer, default=1)
    knowledge_point_ids = Column(JSON, default=list)  # [kp_xxx, kp_yyy]
    is_review_queue = Column(Boolean, default=False)
    answered_at = Column(DateTime, default=now)


class ExamSubmission(Base):
    """One committed ordinary-exam submission per viewer and paper."""
    __tablename__ = "exam_submissions"

    user_id = Column(String(36), primary_key=True)
    paper_id = Column(String(36), ForeignKey("exam_papers.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, nullable=False, default=now)


class ErrorLog(Base):
    __tablename__ = "error_log"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user")
    question_id = Column(String(36), ForeignKey("question_bank.id"), nullable=False)
    error_count = Column(Integer, default=1)
    last_error_at = Column(DateTime, default=now)
    variant_generated = Column(Boolean, default=False)


class QuizProgress(Base):
    """刷题进度持久化（答案 + 当前题号 + 筛选状态等），按用户存一行"""
    __tablename__ = "quiz_progress"

    user_id = Column(String(36), primary_key=True, default="local_user")
    data = Column(JSON, nullable=False, default=dict)  # {answers:{}, wrongList:bool, idx:int, filter:str, updated_at:...}
    updated_at = Column(DateTime, default=now, onupdate=now)


# ==================== Flashcard Models ====================

class Deck(Base):
    __tablename__ = "decks"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    # NULL retains legacy shared decks; manual decks use the trusted viewer ID.
    user_id = Column(String(36), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    card_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)


class Flashcard(Base):
    __tablename__ = "flashcards"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    card_type = Column(String(50), nullable=False)  # qa/cloze/definition/compare
    front = Column(Text, nullable=False)
    back = Column(Text, nullable=False)
    hints = Column(Text)
    tags = Column(JSON, default=list)
    source_node_id = Column(String(36))  # trace back to knowledge tree node
    source_error_id = Column(String(36))  # trace back to error question
    knowledge_point_id = Column(String(64))  # FK to knowledge_point_nodes.id
    review_count = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    last_review_at = Column(DateTime)
    accuracy_rate = Column(Float, default=0.0)
    deck_id = Column(String(36), ForeignKey("decks.id"))
    generation_cache_key = Column(String(128))
    # Content/export versions are independent of FSRS and Anki identity.
    content_version = Column(Integer, nullable=False, default=1, server_default="1")
    anki_export_version = Column(Integer, nullable=True)
    anki_export_mtime = Column(BigInteger, nullable=True)
    anki_export_format = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now)


class ReviewSchedule(Base):
    __tablename__ = "review_schedule"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    card_id = Column(String(36), ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False)
    fsrs_stability = Column(Float)
    fsrs_difficulty = Column(Float)
    fsrs_retrievability = Column(Float)
    fsrs_version = Column(String(32), nullable=True)  # NULL marks legacy estimates
    next_review_at = Column(DateTime)
    last_review_at = Column(DateTime)
    review_count = Column(Integer, default=0)
    state = Column(String(50), default="new")  # new/learning/review/relearning


class ReviewLog(Base):
    __tablename__ = "review_log"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    card_id = Column(String(36), ForeignKey("flashcards.id"), nullable=False)
    rating = Column(Integer)  # 1-4 FSRS self-rating
    review_at = Column(DateTime, default=now)
    time_spent_ms = Column(Integer)


# ==================== Game Models ====================

class GameLevel(Base):
    __tablename__ = "game_levels"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    game_type = Column(String(50), nullable=False)  # matching/ladder/cloze/fix/coding
    level_index = Column(Integer, nullable=False)
    difficulty = Column(String(20), default="medium")
    level_data = Column(JSON, nullable=False, default=dict)
    unlock_condition = Column(JSON)
    generation_cache_key = Column(String(128))


class GameProgress(Base):
    __tablename__ = "game_progress"
    __table_args__ = (UniqueConstraint("user_id", "level_id"),)

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user")
    game_type = Column(String(50), nullable=False)
    level_id = Column(String(36), ForeignKey("game_levels.id"), nullable=False)
    best_score = Column(Integer)
    stars = Column(Integer, default=0)
    completed = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=now, onupdate=now)


# ==================== AI Assistant Models ====================

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user", server_default="local_user", index=True)
    title = Column(String(200))
    role_preset = Column(String(50), nullable=False)  # lecturer/tutor/mentor/expand
    created_at = Column(DateTime, default=now)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    tokens_input = Column(Integer)
    tokens_output = Column(Integer)
    model_used = Column(String(256))
    created_at = Column(DateTime, default=now)

    conversation = relationship("Conversation", back_populates="messages")


# ==================== API Management Models ====================

class APICallLog(Base):
    __tablename__ = "api_call_logs"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), default="local_user")
    task_type = Column(String(50), nullable=False)
    model_name = Column(String(256), nullable=False)
    tokens_input = Column(Integer)
    tokens_output = Column(Integer)
    cost_estimate = Column(Float)
    from_cache = Column(Boolean, default=False)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    content_summary = Column(String(200))  # max 200 chars, NO original content
    duration_ms = Column(Integer)
    created_at = Column(DateTime, default=now)


class APIQuota(Base):
    __tablename__ = "api_quota"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), unique=True, nullable=False, default="local_user")
    daily_limit = Column(Integer, default=1_000_000)
    used_today = Column(Integer, default=0)
    reset_at = Column(Date)
    total_used = Column(Integer, default=0)


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    provider = Column(String(50), nullable=False)  # openai/deepseek/qwen/ernie/kimi/zhipu/ollama
    key_encrypted = Column(String(500), nullable=False)  # AES encrypted
    key_alias = Column(String(100))
    permission_level = Column(String(50), default="student")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)

    user = relationship("User", backref="api_keys")


# ==================== Cache Table (for SQLite-based caching) ====================

class APICache(Base):
    __tablename__ = "api_cache"

    cache_key = Column(String(64), primary_key=True)  # SHA256 hash
    response_content = Column(Text, nullable=False)
    model_used = Column(String(256), nullable=False)
    tokens_input = Column(Integer)
    tokens_output = Column(Integer)
    created_at = Column(DateTime, default=now)
    ttl_days = Column(Integer, default=30)


# ==================== Study Plan Models ====================

class StudyPlan(Base):
    __tablename__ = "study_plans"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user")
    name = Column(String(200), nullable=False)
    description = Column(Text)
    plan_type = Column(String(16), default="long")  # 'short' (1-3天按小时) | 'long' (1-4周按天)
    daily_hours = Column(Float, default=2.0)
    knowledge_point_ids = Column(JSON, default=list)  # 关联知识点ID列表
    ebbinghaus_nodes = Column(JSON, default=list)  # [{day:1,review:true}, {day:2,review:true}, ...]
    short_term_plan = Column(JSON, nullable=True)
    long_term_plan = Column(JSON, nullable=True)
    target_end_date = Column(DateTime)  # 计划截止日期
    progress = Column(Float, default=0.0)  # 0-100
    status = Column(String(50), default="active")  # active/completed/paused
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class StudyGoal(Base):
    __tablename__ = "study_goals"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    plan_id = Column(String(36), ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    priority = Column(String(20), default="medium")  # high/medium/low
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime)
    due_date = Column(DateTime)
    order_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)


class StudyReminder(Base):
    __tablename__ = "study_reminders"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user")
    plan_id = Column(String(36), ForeignKey("study_plans.id", ondelete="CASCADE"))
    goal_id = Column(String(36), ForeignKey("study_goals.id", ondelete="CASCADE"))
    message = Column(Text, nullable=False)
    remind_at = Column(DateTime, nullable=False)
    is_read = Column(Boolean, default=False)
    repeat_daily = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)


# ==================== Knowledge Graph Models ====================

class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    tree_id = Column(String(36), ForeignKey("knowledge_trees.id", ondelete="CASCADE"), nullable=False)
    source_node_id = Column(String(36), nullable=False)
    target_node_id = Column(String(36), nullable=False)
    relation_type = Column(String(50), default="related_to")  # related_to/prerequisite/extends/contradicts/example_of
    description = Column(Text)
    created_at = Column(DateTime, default=now)


# ==================== Share / Collaboration Models ====================

class ShareLink(Base):
    __tablename__ = "share_links"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False, default="local_user")
    resource_type = Column(String(50), nullable=False)  # knowledge_tree/question_bank/flashcard_deck
    resource_id = Column(String(36), nullable=False)
    access_code = Column(String(10), nullable=False)  # 6-digit access code
    expires_at = Column(DateTime)  # None = never expires
    view_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)


# ==================== Knowledge Summary Models ====================

class KnowledgeSummary(Base):
    """完整知识点总结（Markdown格式）"""
    __tablename__ = "knowledge_summaries"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    document_ids = Column(JSON, default=list)  # 所有源文档 ID 列表
    content_md = Column(Text, nullable=False)  # 完整 Markdown 内容
    node_count = Column(Integer, default=0)
    level_stats = Column(JSON, default=dict)  # {"L1":3,"L2":12,"L3":45}
    model_used = Column(String(256))
    generation_cache_key = Column(String(128), unique=True)
    generated_at = Column(DateTime, default=now)
    created_at = Column(DateTime, default=now)


class KnowledgePointNode(Base):
    """知识点节点（结构化拆分，3级层级）"""
    __tablename__ = "knowledge_point_nodes"

    id = Column(String(64), primary_key=True)  # kp_{doc_id}_L{level}_{seq}
    summary_id = Column(String(36), ForeignKey("knowledge_summaries.id", ondelete="CASCADE"))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(String(64))
    level = Column(Integer, nullable=False)  # 1/2/3
    sequence = Column(Integer, nullable=False)  # 同级排序序号
    title = Column(String(256), nullable=False)
    explanation = Column(Text, nullable=False)  # 详细解释
    related_concepts = Column(Text)  # 关联概念（以逗号分隔或 JSON 数组）
    examples = Column(Text)  # 示例（Markdown 格式）
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=now)


# ==================== Knowledge Coverage Model ====================

class KnowledgeCoverage(Base):
    """知识点覆盖率映射（知识点↔题目/记忆卡多对多关系）"""
    __tablename__ = "knowledge_coverage"
    __table_args__ = (
        UniqueConstraint("knowledge_point_id", "resource_type", "resource_id"),
    )

    id = Column(String(36), primary_key=True, default=gen_uuid)
    knowledge_point_id = Column(String(64), ForeignKey("knowledge_point_nodes.id", ondelete="CASCADE"), nullable=False)
    resource_type = Column(String(32), nullable=False)  # 'question' | 'flashcard'
    resource_id = Column(String(36), nullable=False)  # question_bank.id 或 flashcards.id
    is_primary = Column(Boolean, default=True)  # 是否为主要覆盖
    created_at = Column(DateTime, default=now)


# ==================== Review Queue Model ====================

class ReviewQueue(Base):
    """复习推送队列（薄弱环节自动推送）"""
    __tablename__ = "review_queue"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default="local_user")
    resource_type = Column(String(32), nullable=False)  # 'flashcard' | 'question'
    resource_id = Column(String(36), nullable=False)
    knowledge_point_id = Column(String(64))  # 关联知识点 ID
    priority = Column(Integer, default=0)  # 优先级（正确率越低越高）
    reason = Column(String(64))  # 'low_accuracy' | 'overdue' | 'manual'
    pushed_at = Column(DateTime, default=now)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime)


# ==================== Recitation (背诵) Models ====================

class RecitationPassage(Base):
    """背诵段落"""
    __tablename__ = "recitation_passages"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)               # 原文
    source = Column(String(200))                         # 来源（古诗词/课文/手动输入）
    category = Column(String(50), default="custom")      # poetry/textbook/custom
    difficulty = Column(Integer, default=1)               # 1-5
    tags = Column(JSON, default=list)
    knowledge_point_id = Column(String(64))              # 关联知识点（可选）
    user_id = Column(String(36), default="local_user")
    created_at = Column(DateTime, default=now)


class RecitationRecord(Base):
    """背诵/默写记录"""
    __tablename__ = "recitation_records"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    passage_id = Column(String(36), ForeignKey("recitation_passages.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String(20), nullable=False)            # "hide" | "dictation"
    accuracy = Column(Float, default=0.0)                # 0-1 准确率
    user_input = Column(Text)                            # 用户输入（默写模式）
    diff_result = Column(JSON)                           # diff 对比结果
    time_spent_ms = Column(Integer, default=0)
    # FSRS 字段
    stability = Column(Float, default=0.0)
    difficulty_score = Column(Float, default=0.0)
    fsrs_version = Column(String(32), nullable=True)
    last_review_at = Column(DateTime, nullable=True)
    state = Column(String(20), default="new")            # new/learning/review/relearning
    next_review_at = Column(DateTime)
    review_count = Column(Integer, default=0)
    user_id = Column(String(36), default="local_user")
    created_at = Column(DateTime, default=now)


# ==================== Language Vocabulary Model ====================

class LanguageVocabulary(Base):
    """生词表（外语学习 Agent 生成）"""
    __tablename__ = "language_vocabulary"
    __table_args__ = (
        UniqueConstraint("document_id", "word"),
    )

    id = Column(String(36), primary_key=True, default=gen_uuid)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    word = Column(String(128), nullable=False)
    phonetic = Column(String(64))  # 音标
    part_of_speech = Column(String(32))  # 词性
    definition = Column(Text, nullable=False)  # 释义
    example_sentence = Column(Text)  # 例句
    difficulty = Column(String(16), default="medium")  # easy/medium/hard
    knowledge_point_id = Column(String(64))  # 关联知识点
    mastered = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)


# ==================== Sync Persistence Models ====================

class SyncOfflineMessage(Base):
    """离线消息持久化（替代 Redis user:{uid}:offline_msgs）"""
    __tablename__ = "sync_offline_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=False, index=True)
    room_id = Column(String(64), default="")
    msg_type = Column(String(32), nullable=False)
    msg_data = Column(JSON, default=dict)
    version = Column(Integer, default=0)
    delivered = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)


class SyncOpLog(Base):
    """操作日志（替代 Redis room:{rid}:event_log）"""
    __tablename__ = "sync_op_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    user_id = Column(String(36), default="")
    operation = Column(String(32), default="")
    data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now)


class SyncFileVersion(Base):
    """文件版本历史持久化"""
    __tablename__ = "sync_file_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(36), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    filename = Column(String(256))
    file_size = Column(Integer, default=0)
    storage_path = Column(String(500))
    updated_by = Column(String(64), default="")
    created_at = Column(DateTime, default=now)


# ==================== Live Session (实时对话考试) Models ====================

class LiveSession(Base):
    """实时对话会话"""
    __tablename__ = "live_sessions"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    host_id = Column(String(36), nullable=False, default="local_user")
    title = Column(String(200), nullable=False)
    room_code = Column(String(8), unique=True, nullable=False)     # 6位房间码
    knowledge_tree_id = Column(String(36))                         # 关联知识树（可选）
    status = Column(String(20), default="waiting")                 # waiting/active/ended
    created_at = Column(DateTime, default=now)
    ended_at = Column(DateTime)


class LiveParticipant(Base):
    """会话参与者"""
    __tablename__ = "live_participants"
    __table_args__ = (UniqueConstraint("session_id", "user_id"),)

    id = Column(String(36), primary_key=True, default=gen_uuid)
    session_id = Column(String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), nullable=False, default="local_user")
    user_name = Column(String(100), default="匿名")
    score = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    total_answered = Column(Integer, default=0)
    joined_at = Column(DateTime, default=now)


class LiveQuestion(Base):
    """实时推送的考题"""
    __tablename__ = "live_questions"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    session_id = Column(String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(String(36), ForeignKey("question_bank.id"), nullable=False)
    question_index = Column(Integer, nullable=False)
    mode = Column(String(20), default="broadcast")                 # broadcast(全员)/spotlight(抽查)
    target_participant_id = Column(String(36))                     # spotlight 模式目标
    status = Column(String(20), default="pending")                 # pending/active/answered
    pushed_at = Column(DateTime, default=now)


class LiveAnswer(Base):
    """实时作答记录"""
    __tablename__ = "live_answers"
    __table_args__ = (UniqueConstraint("live_question_id", "participant_id"),)

    id = Column(String(36), primary_key=True, default=gen_uuid)
    live_question_id = Column(String(36), ForeignKey("live_questions.id", ondelete="CASCADE"), nullable=False)
    participant_id = Column(String(36), ForeignKey("live_participants.id", ondelete="CASCADE"), nullable=False)
    user_answer = Column(Text)
    is_correct = Column(Boolean, default=False)
    time_spent_ms = Column(Integer, default=0)
    answered_at = Column(DateTime, default=now)


# ==================== Reading Language Models ====================
from app.models.subscription import (  # noqa: E402, F401
    UserTier, TierConfig, License, LicenseActivation, PaymentOrder,
)
from app.models.system_config import SystemConfig  # noqa: E402, F401
from app.models.gamification import LearningStreak, Achievement, FocusSession, DailyCheckIn, ACHIEVEMENTS  # noqa: E402, F401

# ==================== Reading Language Models ====================

class ReadingArticle(Base):
    """阅读语言 - 用户上传的文章"""
    __tablename__ = "reading_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    char_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)


class ReadingConversionCache(Base):
    """阅读语言 - 转换结果缓存"""
    __tablename__ = "reading_conversion_cache"

    cache_key = Column(String(64), primary_key=True)
    result = Column(Text, nullable=False)
    vocabulary = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, default=now)


# ==================== Glossary Models ====================
from app.models.glossary import GlossaryTerm  # noqa: E402, F401

from app.models.knowledge_network import NetworkConcept, ExtractionJob, ConceptSource, ConceptRelation, ConceptLearning  # noqa: E402, F401

from app.models.knowledge_authority import AuthoritySourceSearch  # noqa: E402, F401
from app.models.material_state import MaterialState, MaterialStateOperation  # noqa: E402, F401
