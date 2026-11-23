from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.prompt import PromptTemplate
from app.schemas.prompt import PromptTemplateInput, PromptTemplateResponse

router = APIRouter(prefix="/prompts", tags=["Prompts"])


async def get_prompt(prompt_id: str, db: AsyncSession = Depends(get_db)) -> PromptTemplate:
    prompt = await db.get(PromptTemplate, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="Prompt template not found")
    return prompt


async def _save_prompt(db: AsyncSession, prompt: PromptTemplate) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="A prompt with this name already exists"
        ) from exc
    await db.refresh(prompt)


@router.get("", response_model=list[PromptTemplateResponse])
async def list_prompts(db: AsyncSession = Depends(get_db)) -> list[PromptTemplate]:
    return list(await db.scalars(select(PromptTemplate).order_by(PromptTemplate.name)))


@router.post("", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_prompt(
    payload: PromptTemplateInput, db: AsyncSession = Depends(get_db)
) -> PromptTemplate:
    prompt = PromptTemplate(name=payload.name, content=payload.content)
    db.add(prompt)
    await _save_prompt(db, prompt)
    return prompt


@router.put("/{prompt_id}", response_model=PromptTemplateResponse)
async def update_prompt(
    payload: PromptTemplateInput,
    prompt: PromptTemplate = Depends(get_prompt),
    db: AsyncSession = Depends(get_db),
) -> PromptTemplate:
    prompt.name = payload.name
    prompt.content = payload.content
    await _save_prompt(db, prompt)
    return prompt


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    prompt: PromptTemplate = Depends(get_prompt), db: AsyncSession = Depends(get_db)
) -> None:
    await db.delete(prompt)
