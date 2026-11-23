from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import mask_secret, secret_box
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.embed_client import OpenAICompatEmbeddingClient
from app.engine.llm.rerank_client import RerankClient
from app.engine.llm.types import LLMMessage, LLMModelConfig
from app.models.llm import LLMModel, ModelType, TokenUsageLog
from app.schemas.llm import (
    LLMModelCreate,
    LLMModelListResponse,
    LLMModelResponse,
    LLMModelTestRequest,
    LLMModelTestResponse,
    LLMModelUnsavedTestRequest,
    LLMModelUpdate,
    LLMUsageSummary,
)

router = APIRouter(prefix="/models", tags=["LLM Models"])


def _to_response(model: LLMModel) -> LLMModelResponse:
    raw_key = secret_box.decrypt(model.api_key_encrypted)
    return LLMModelResponse(
        id=model.id,
        name=model.name,
        model_type=ModelType(model.model_type),
        provider=model.provider,
        base_url=model.base_url,
        model_id=model.model_id,
        description=model.description,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        temperature=model.temperature,
        input_price_per_1m=model.input_price_per_1m,
        output_price_per_1m=model.output_price_per_1m,
        extra_params=model.extra_params or {},
        is_active=model.is_active,
        is_default=model.is_default,
        api_key_masked=mask_secret(raw_key),
        has_api_key=bool(raw_key),
        last_test_status=model.last_test_status,
        last_test_latency_ms=model.last_test_latency_ms,
        last_test_message=model.last_test_message,
        last_tested_at=model.last_tested_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


async def _run_live_model_test(
    *,
    model_type: ModelType,
    runtime_cfg: LLMModelConfig,
    test_input: LLMModelTestRequest,
) -> LLMModelTestResponse:
    """Execute a real connectivity & inference probe against Chat, Embedding, or Rerank API."""
    start = time.monotonic()
    try:
        if model_type == ModelType.CHAT:
            client = OpenAICompatChatClient(runtime_cfg)
            try:
                resp = await client.chat(
                    [LLMMessage(role="user", content=test_input.prompt)],
                    max_tokens=min(256, runtime_cfg.max_output_tokens),
                )
            finally:
                await client.close()
            return LLMModelTestResponse(
                ok=True,
                model_type=model_type,
                latency_ms=resp.latency_ms,
                detail="Chat completion succeeded.",
                preview={
                    "reply": resp.content,
                    "reasoning": resp.reasoning_content,
                    "resolved_model": resp.model,
                    "finish_reason": resp.finish_reason,
                },
                usage=resp.usage.model_dump(),
            )

        if model_type == ModelType.EMBEDDING:
            embed_client = OpenAICompatEmbeddingClient(runtime_cfg)
            try:
                res = await embed_client.embed([test_input.prompt])
            finally:
                await embed_client.close()
            first_vec = res.vectors[0] if res.vectors else []
            return LLMModelTestResponse(
                ok=True,
                model_type=model_type,
                latency_ms=res.latency_ms,
                detail=f"Embedding succeeded ({res.dimensions} dimensions).",
                preview={
                    "dimensions": res.dimensions,
                    "vector_head": [round(v, 6) for v in first_vec[:8]],
                    "resolved_model": res.model,
                },
                usage=res.usage.model_dump(),
            )

        # Rerank
        rerank_client = RerankClient(runtime_cfg)
        try:
            rr = await rerank_client.rerank(test_input.query, test_input.documents)
        finally:
            await rerank_client.close()
        return LLMModelTestResponse(
            ok=True,
            model_type=model_type,
            latency_ms=rr.latency_ms,
            detail=f"Rerank succeeded ({len(rr.items)} scored documents).",
            preview={
                "query": test_input.query,
                "resolved_model": rr.model,
                "results": [
                    {
                        "index": item.index,
                        "score": round(item.score, 6),
                        "document": item.document[:160],
                    }
                    for item in rr.items
                ],
            },
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return LLMModelTestResponse(
            ok=False,
            model_type=model_type,
            latency_ms=elapsed_ms,
            detail=f"{type(exc).__name__}: {exc}",
            preview={},
        )


@router.get("", response_model=LLMModelListResponse)
async def list_models(
    model_type: ModelType | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> LLMModelListResponse:
    # Global category counts for KPI tiles and tab badges
    cat_rows = (
        await db.execute(
            select(LLMModel.model_type, func.count(LLMModel.id)).group_by(LLMModel.model_type)
        )
    ).all()
    category_counts = {"all": 0, "chat": 0, "embedding": 0, "rerank": 0}
    for m_type, cnt in cat_rows:
        category_counts[str(m_type)] = int(cnt)
        category_counts["all"] += int(cnt)

    filters = []
    if model_type is not None:
        filters.append(LLMModel.model_type == model_type.value)
    if is_active is not None:
        filters.append(LLMModel.is_active == is_active)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                LLMModel.name.ilike(pattern),
                LLMModel.model_id.ilike(pattern),
                LLMModel.base_url.ilike(pattern),
                LLMModel.provider.ilike(pattern),
            )
        )

    count_stmt = select(func.count(LLMModel.id))
    data_stmt = select(LLMModel).order_by(LLMModel.is_default.desc(), LLMModel.created_at.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        data_stmt = data_stmt.where(*filters)

    total = int((await db.scalar(count_stmt)) or 0)
    offset = (page - 1) * page_size
    rows = list((await db.execute(data_stmt.offset(offset).limit(page_size))).scalars().all())

    return LLMModelListResponse.create(
        items=[_to_response(m) for m in rows],
        total=total,
        page=page,
        page_size=page_size,
        category_counts=category_counts,
    )


@router.get("/usage-stats", response_model=LLMUsageSummary)
async def get_usage_stats(db: AsyncSession = Depends(get_db)) -> LLMUsageSummary:
    agg_stmt = select(
        func.count(TokenUsageLog.id),
        func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
        func.coalesce(func.sum(TokenUsageLog.cost_usd), 0.0),
    )
    row = (await db.execute(agg_stmt)).one()

    by_model_stmt = (
        select(
            TokenUsageLog.model_id,
            TokenUsageLog.model_type,
            func.count(TokenUsageLog.id),
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0),
            func.coalesce(func.sum(TokenUsageLog.cost_usd), 0.0),
        )
        .group_by(TokenUsageLog.model_id, TokenUsageLog.model_type)
        .order_by(func.sum(TokenUsageLog.total_tokens).desc())
    )
    by_model_rows = (await db.execute(by_model_stmt)).all()

    return LLMUsageSummary(
        total_calls=int(row[0]),
        total_prompt_tokens=int(row[1]),
        total_completion_tokens=int(row[2]),
        total_tokens=int(row[3]),
        total_cost_usd=round(float(row[4]), 6),
        by_model=[
            {
                "model_id": r[0],
                "model_type": r[1],
                "calls": int(r[2]),
                "total_tokens": int(r[3]),
                "cost_usd": round(float(r[4]), 6),
            }
            for r in by_model_rows
        ],
    )


@router.post("/test-unsaved", response_model=LLMModelTestResponse)
async def test_unsaved_model(payload: LLMModelUnsavedTestRequest) -> LLMModelTestResponse:
    """Test connectivity directly from form inputs before saving to database."""
    runtime_cfg = LLMModelConfig(
        name=payload.name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model_id=payload.model_id,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        temperature=payload.temperature,
        input_price_per_1m=payload.input_price_per_1m,
        output_price_per_1m=payload.output_price_per_1m,
        timeout_sec=30.0,
        max_retries=1,
        extra_params=payload.extra_params,
    )
    return await _run_live_model_test(
        model_type=payload.model_type,
        runtime_cfg=runtime_cfg,
        test_input=payload.test_input,
    )


@router.post("", response_model=LLMModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: LLMModelCreate,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    existing = await db.execute(select(LLMModel).where(LLMModel.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A model named '{payload.name}' already exists.",
        )

    # Auto-set as default if it's the first model of its category or requested
    if payload.is_default:
        await db.execute(
            update(LLMModel)
            .where(LLMModel.model_type == payload.model_type.value)
            .values(is_default=False)
        )
    else:
        same_type_count = await db.scalar(
            select(func.count(LLMModel.id)).where(
                LLMModel.model_type == payload.model_type.value
            )
        )
        if not same_type_count:
            payload.is_default = True

    model = LLMModel(
        name=payload.name,
        model_type=payload.model_type.value,
        provider=payload.provider,
        base_url=payload.base_url,
        api_key_encrypted=secret_box.encrypt(payload.api_key),
        model_id=payload.model_id,
        description=payload.description,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        temperature=payload.temperature,
        input_price_per_1m=payload.input_price_per_1m,
        output_price_per_1m=payload.output_price_per_1m,
        extra_params=payload.extra_params,
        is_active=payload.is_active,
        is_default=payload.is_default,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)
    return _to_response(model)


@router.get("/{model_id}", response_model=LLMModelResponse)
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    return _to_response(model)


@router.put("/{model_id}", response_model=LLMModelResponse)
async def update_model(
    model_id: str,
    payload: LLMModelUpdate,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")

    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] != model.name:
        dup = await db.execute(select(LLMModel).where(LLMModel.name == update_data["name"]))
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A model named '{update_data['name']}' already exists.",
            )

    # Preserve existing encrypted API key if omitted or blank string
    raw_api_key = update_data.pop("api_key", None)
    if raw_api_key is not None and raw_api_key != "":
        model.api_key_encrypted = secret_box.encrypt(raw_api_key)

    new_type = update_data.get("model_type")
    target_type_val = new_type.value if isinstance(new_type, ModelType) else model.model_type

    if update_data.get("is_default") is True:
        await db.execute(
            update(LLMModel)
            .where(LLMModel.model_type == target_type_val, LLMModel.id != model.id)
            .values(is_default=False)
        )

    for key, val in update_data.items():
        if key == "model_type" and isinstance(val, ModelType):
            setattr(model, key, val.value)
        else:
            setattr(model, key, val)

    await db.flush()
    await db.refresh(model)
    return _to_response(model)


@router.patch("/{model_id}/toggle", response_model=LLMModelResponse)
async def toggle_model_active(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    model.is_active = not model.is_active
    await db.flush()
    await db.refresh(model)
    return _to_response(model)


@router.patch("/{model_id}/default", response_model=LLMModelResponse)
async def set_default_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    await db.execute(
        update(LLMModel)
        .where(LLMModel.model_type == model.model_type)
        .values(is_default=False)
    )
    model.is_default = True
    await db.flush()
    await db.refresh(model)
    return _to_response(model)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    await db.delete(model)


@router.post("/{model_id}/test", response_model=LLMModelTestResponse)
async def test_saved_model(
    model_id: str,
    test_input: LLMModelTestRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> LLMModelTestResponse:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")

    runtime_cfg = LLMModelConfig(
        name=model.name,
        base_url=model.base_url,
        api_key=secret_box.decrypt(model.api_key_encrypted),
        model_id=model.model_id,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        temperature=model.temperature,
        input_price_per_1m=model.input_price_per_1m,
        output_price_per_1m=model.output_price_per_1m,
        timeout_sec=30.0,
        max_retries=1,
        extra_params=model.extra_params or {},
    )
    probe = test_input or LLMModelTestRequest()
    result = await _run_live_model_test(
        model_type=ModelType(model.model_type),
        runtime_cfg=runtime_cfg,
        test_input=probe,
    )

    model.last_test_status = "ok" if result.ok else "error"
    model.last_test_latency_ms = result.latency_ms
    model.last_test_message = result.detail[:500]
    model.last_tested_at = datetime.now(UTC)

    # Record token usage log if tokens were consumed during test
    if result.ok and result.usage:
        db.add(
            TokenUsageLog(
                model_db_id=model.id,
                model_id=model.model_id,
                model_type=model.model_type,
                prompt_tokens=int(result.usage.get("prompt_tokens", 0)),
                completion_tokens=int(result.usage.get("completion_tokens", 0)),
                total_tokens=int(result.usage.get("total_tokens", 0)),
                cost_usd=float(result.usage.get("cost_usd", 0.0)),
                latency_ms=result.latency_ms,
            )
        )

    await db.flush()
    return result
