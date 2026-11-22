"""API routes package"""
from app.api.flashcards import router as flashcards_router
from app.api.chat import router as chat_router
from app.api.admin import router as admin_router
from app.api.search import router as search_router
from app.api.pipeline import router as pipeline_router
from app.api.stats import router as stats_router
from app.api.backup import router as backup_router
from app.api.game import router as game_router
from app.api.study import router as study_router
from app.api.share import router as share_router
from app.api.auth import router as auth_router
from app.api.knowledge_points import router as kp_router
from app.api.user import router as user_router
from app.api.notifications import router as notifications_router
from app.api.reading import router as reading_router
from app.api.recitation import router as recitation_router
from app.api.live_session import router as live_session_router
from app.api.voice import router as voice_router

__all__ = [
    "flashcards_router", "chat_router", "admin_router",
    "search_router", "pipeline_router", "stats_router",
    "backup_router", "game_router", "study_router", "share_router",
    "auth_router", "kp_router", "user_router", "notifications_router",
    "reading_router", "recitation_router", "live_session_router", "voice_router",
]
