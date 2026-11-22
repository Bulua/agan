from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.api.ws.sessions import ws_router
from app.core.config import settings
from app.core.database import async_session_factory, init_db
from app.engine.chat_runner import chat_run_manager
from app.engine.session_runner import session_runner
from app.engine.tools.db_loader import seed_builtin_tools


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    async with async_session_factory() as db:
        await seed_builtin_tools(db)
        await db.commit()
    session_runner.register_persistence_hook()
    await session_runner.mark_stale_sessions_stopped()
    yield
    await chat_run_manager.shutdown()
    await session_runner.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description="Automated AI CTF Solving Platform API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router)
    app.include_router(ws_router)
    return app


app = create_app()
