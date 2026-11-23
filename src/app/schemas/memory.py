from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MemoryScopeType = Literal["global", "agent", "shared"]


class MemoryFileItemDTO(BaseModel):
    path: str
    title: str
    memory_type: str = "note"
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    author: str = ""
    size: int = 0
    is_index: bool = False
    updated_at: datetime


class MemoryScopeGroupDTO(BaseModel):
    scope: MemoryScopeType
    scope_id: str = ""
    label: str
    subtitle: str = ""
    file_count: int = 0
    total_size: int = 0
    files: list[MemoryFileItemDTO] = Field(default_factory=list)


class MemoryScopesOverviewDTO(BaseModel):
    root_dir: str
    global_scope: MemoryScopeGroupDTO
    agent_scopes: list[MemoryScopeGroupDTO] = Field(default_factory=list)
    shared_scopes: list[MemoryScopeGroupDTO] = Field(default_factory=list)


class MemoryFileContentDTO(BaseModel):
    scope: MemoryScopeType
    scope_id: str = ""
    path: str
    content: str
    title: str
    memory_type: str = "note"
    tags: list[str] = Field(default_factory=list)
    author: str = ""
    summary: str = ""
    size: int = 0
    is_index: bool = False
    updated_at: datetime


class MemoryFileSaveRequest(BaseModel):
    scope: MemoryScopeType
    scope_id: str = ""
    path: str = Field(..., min_length=1, max_length=240)
    content: str = Field(default="")
    title: str = Field(default="", max_length=160)
    memory_type: str = Field(default="note", max_length=40)
    tags: list[str] = Field(default_factory=list)
    raw_mode: bool = True


class MemorySearchHitDTO(BaseModel):
    scope: str
    scope_id: str = ""
    scope_label: str = ""
    path: str
    title: str
    memory_type: str = "note"
    tags: list[str] = Field(default_factory=list)
    author: str = ""
    snippet: str = ""
    score: int = 0


class MemorySearchResponseDTO(BaseModel):
    query: str
    total: int
    items: list[MemorySearchHitDTO] = Field(default_factory=list)
