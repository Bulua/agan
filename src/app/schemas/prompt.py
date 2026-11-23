from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, field_validator

from app.schemas.agent import SYSTEM_PROMPT_MAX_LENGTH


class PromptTemplateInput(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    content: str = Field(min_length=1, max_length=SYSTEM_PROMPT_MAX_LENGTH)

    @field_validator("content")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt content cannot be blank")
        return value


class PromptTemplateResponse(PromptTemplateInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def restore_utc_timezone(cls, value: datetime) -> datetime:
        # SQLite returns UTC timestamps without their timezone.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @computed_field
    @property
    def character_count(self) -> int:
        return len(self.content)
