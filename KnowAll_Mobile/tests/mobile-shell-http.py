"""Real production routes with a temporary SQL database and fictional user.

Parsing, JWT/passwords and CRUD run normally. Extraction and all AI are blocked.
No production database, uploaded assets or model credentials are used.
"""
import os
import secrets
import sys
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

root = Path(tempfile.mkdtemp(prefix="knowall-shell-01a12efd-"))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (root / "test.sqlite").as_posix()
os.environ["JWT_SECRET"] = secrets.token_hex(32)
os.environ["KNOWALL_DATA_ROOT"] = str(root)
os.environ["DOCUMENT_DIR"] = str(root / "documents")
os.environ["DEBUG"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "KnowAll_Studio/backend"))

from fastapi import FastAPI, Request
from app.database import Base, engine, async_session
from app.models import User, Deck, Flashcard, ReviewSchedule, StudyPlan, StudyGoal
from app.models.notification import Notification
from app.models.user_history import UserHistory
from app.core.auth import hash_password
from app.core.api_scheduler import api_client
from app.api import auth, documents, flashcards, study, user, notifications, answer_review, learning

ai_calls = 0
trace = []
async def forbid_ai(*args, **kwargs):
    global ai_calls
    ai_calls += 1
    raise AssertionError("AI forbidden in isolated shell acceptance")
api_client.generate = forbid_ai

async def no_extraction(response, *args):
    return response
documents._with_extraction = no_extraction

@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_session() as db:
        db.add(User(id="shell-http", username="shell_http", nickname="小林", email="shell@example.invalid",
                    password_hash=hash_password("LocalShellTest_2026!"), is_active=True))
        db.add(Deck(id="shell-deck", user_id="shell-http", name="隔离知识卡", card_count=1))
        db.add(Flashcard(id="shell-card", deck_id="shell-deck", card_type="qa",
                         front="细胞膜的主要作用是什么？", back="调节物质交换，接收外界信号。"))
        db.add(ReviewSchedule(id="shell-schedule", card_id="shell-card", state="new", review_count=0))
        db.add(StudyPlan(id="shell-plan", user_id="shell-http", name="期末复习 · 细胞生物学", progress=0))
        db.add(StudyGoal(id="shell-goal", plan_id="shell-plan", title="复习第一章", completed=False))
        for i in range(6):
            db.add(Notification(id="n" + str(i), user_id="shell-http", title="复习提醒 " + str(i),
                                content="回顾细胞结构。", category="study", is_read=False))
            db.add(UserHistory(id="h" + str(i), user_id="shell-http", action_type="quiz", action_label="完成练习 " + str(i)))
        await db.commit()
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
for router in [auth.router, documents.router, flashcards.router, study.router, user.router, notifications.router, answer_review.router, learning.router]:
    app.include_router(router)

@app.middleware("http")
async def log(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        trace.append({"path": request.url.path, "method": request.method, "status": response.status_code})
    return response

@app.get("/__test/health")
async def health():
    return {"isolated": True, "ai_calls": ai_calls, "requests": trace}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8106, log_level="warning")
