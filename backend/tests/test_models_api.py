from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import create_app


@pytest.fixture
async def api_client(tmp_path):
    db_file = tmp_path / "test_models.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.llm  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()

    async def override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_llm_models_crud_encryption_and_default_switch(api_client: AsyncClient) -> None:
    # 1. Health check
    h = await api_client.get("/api/v1/health")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"

    # 2. Create first Chat model (auto-marked default)
    r1 = await api_client.post(
        "/api/v1/models",
        json={
            "name": "DeepSeek Chat",
            "model_type": "chat",
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1/",
            "api_key": "sk-secret1234567890abcd",
            "model_id": "deepseek-chat",
            "context_window": 64000,
            "temperature": 0.2,
            "input_price_per_1m": 0.27,
            "output_price_per_1m": 1.10,
        },
    )
    assert r1.status_code == 201
    m1 = r1.json()
    assert m1["base_url"] == "https://api.deepseek.com/v1"
    assert m1["api_key_masked"] == "sk-****abcd"
    assert m1["has_api_key"] is True
    assert m1["is_default"] is True

    # 3. Create Embedding model
    r2 = await api_client.post(
        "/api/v1/models",
        json={
            "name": "BGE M3 Embed",
            "model_type": "embedding",
            "provider": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": "sk-embed9999xxxx",
            "model_id": "BAAI/bge-m3",
            "context_window": 8192,
        },
    )
    assert r2.status_code == 201
    m2 = r2.json()
    assert m2["model_type"] == "embedding"
    assert m2["is_default"] is True

    # 4. Create second Chat model & set as default
    r3 = await api_client.post(
        "/api/v1/models",
        json={
            "name": "GPT-4o",
            "model_type": "chat",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-openai12345678",
            "model_id": "gpt-4o",
            "is_default": True,
        },
    )
    assert r3.status_code == 201
    m3 = r3.json()
    assert m3["is_default"] is True

    # Previous chat model should no longer be default
    refreshed_m1 = (await api_client.get(f"/api/v1/models/{m1['id']}")).json()
    assert refreshed_m1["is_default"] is False

    # 5. Update without api_key preserves existing encrypted key
    upd = await api_client.put(
        f"/api/v1/models/{m1['id']}",
        json={"temperature": 0.5, "api_key": ""},
    )
    assert upd.status_code == 200
    assert upd.json()["temperature"] == 0.5
    assert upd.json()["api_key_masked"] == "sk-****abcd"

    # 6. Filter by model_type + server-side pagination + category_counts
    chats_page1 = (await api_client.get("/api/v1/models?model_type=chat&page=1&page_size=1")).json()
    assert chats_page1["total"] == 2
    assert chats_page1["page"] == 1
    assert chats_page1["page_size"] == 1
    assert chats_page1["total_pages"] == 2
    assert len(chats_page1["items"]) == 1
    assert chats_page1["category_counts"]["all"] == 3
    assert chats_page1["category_counts"]["chat"] == 2
    assert chats_page1["category_counts"]["embedding"] == 1

    chats_page2 = (await api_client.get("/api/v1/models?model_type=chat&page=2&page_size=1")).json()
    assert len(chats_page2["items"]) == 1
    assert chats_page2["items"][0]["id"] != chats_page1["items"][0]["id"]

    # Search filter
    search_res = (await api_client.get("/api/v1/models?search=deepseek")).json()
    assert search_res["total"] == 1
    assert search_res["items"][0]["model_id"] == "deepseek-chat"

    # 7. Toggle active state
    tog = await api_client.patch(f"/api/v1/models/{m1['id']}/toggle")
    assert tog.status_code == 200
    assert tog.json()["is_active"] is False

    # 8. Usage stats endpoint works cleanly
    stats = await api_client.get("/api/v1/models/usage-stats")
    assert stats.status_code == 200
    assert stats.json()["total_calls"] == 0

    # 9. Delete model
    dele = await api_client.delete(f"/api/v1/models/{m2['id']}")
    assert dele.status_code == 204
    assert (await api_client.get(f"/api/v1/models/{m2['id']}")).status_code == 404
