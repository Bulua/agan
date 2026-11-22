from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SkillFileNode(BaseModel):
    name: str
    path: str
    kind: str  # "dir" | "file"
    size: int = 0
    children: list[SkillFileNode] = Field(default_factory=list)


SkillFileNode.model_rebuild()


class SkillSummaryDTO(BaseModel):
    name: str
    title: str
    description: str = ""
    has_skill_md: bool = True
    file_count: int = 0
    total_size: int = 0
    updated_at: datetime
    tree: list[SkillFileNode] = Field(default_factory=list)


class SkillListDTO(BaseModel):
    root_dir: str
    items: list[SkillSummaryDTO] = Field(default_factory=list)
    total: int = 0


class SkillFileContentDTO(BaseModel):
    skill_name: str
    path: str
    content: str
    size: int
    is_binary: bool = False
    truncated: bool = False


class SkillFileWriteRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    content: str = Field(default="")


class SkillCreateDirRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    content_md: str = Field(default="")
