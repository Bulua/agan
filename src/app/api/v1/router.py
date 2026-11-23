from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.agents import router as agents_router
from app.api.v1.chat import router as chat_router
from app.api.v1.mcp import router as mcp_router
from app.api.v1.memory import router as memory_router
from app.api.v1.models import router as models_router
from app.api.v1.prompts import router as prompts_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.skills import router as skills_router
from app.api.v1.tools import router as tools_router

api_v1_router = APIRouter(prefix="/api/v1")


@api_v1_router.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "agan-ctf-backend", "version": "0.1.0"}


api_v1_router.include_router(chat_router)
api_v1_router.include_router(models_router)
api_v1_router.include_router(tools_router)
api_v1_router.include_router(mcp_router)
api_v1_router.include_router(memory_router)
api_v1_router.include_router(prompts_router)
api_v1_router.include_router(skills_router)
api_v1_router.include_router(agents_router)
api_v1_router.include_router(sessions_router)
