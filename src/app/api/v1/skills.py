from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.core.config import settings
from app.engine.skills.fs_loader import (
    SkillSummary,
    SkillTreeNode,
    create_skill_dir,
    delete_skill_dir,
    delete_skill_file,
    get_skill_summary,
    import_skill_zip,
    list_skills,
    read_skill_file,
    write_skill_file,
)
from app.schemas.skill import (
    SkillCreateDirRequest,
    SkillFileContentDTO,
    SkillFileNode,
    SkillFileWriteRequest,
    SkillListDTO,
    SkillSummaryDTO,
)

router = APIRouter(prefix="/skills", tags=["Skills"])


def _node_to_dto(node: SkillTreeNode) -> SkillFileNode:
    return SkillFileNode(
        name=node.name,
        path=node.path,
        kind=node.kind,
        size=node.size,
        children=[_node_to_dto(c) for c in node.children],
    )


def _summary_to_dto(summary: SkillSummary) -> SkillSummaryDTO:
    return SkillSummaryDTO(
        name=summary.name,
        title=summary.title,
        description=summary.description,
        has_skill_md=summary.has_skill_md,
        file_count=summary.file_count,
        total_size=summary.total_size,
        updated_at=summary.updated_at,
        tree=[_node_to_dto(n) for n in summary.tree],
    )


@router.get("", response_model=SkillListDTO)
async def list_filesystem_skills(
    search: str | None = Query(default=None),
) -> SkillListDTO:
    """List all skill folders directly from `src/skills/` with their nested file trees."""
    root = settings.resolved_skills_dir
    summaries = list_skills(root, search=search)
    return SkillListDTO(
        root_dir=str(root),
        items=[_summary_to_dto(s) for s in summaries],
        total=len(summaries),
    )


@router.post("", response_model=SkillSummaryDTO, status_code=status.HTTP_201_CREATED)
async def create_filesystem_skill(payload: SkillCreateDirRequest) -> SkillSummaryDTO:
    """Create a new skill folder containing a starter `SKILL.md`."""
    try:
        summary = create_skill_dir(
            settings.resolved_skills_dir,
            payload.name,
            description=payload.description,
            content_md=payload.content_md,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _summary_to_dto(summary)


@router.post("/upload-zip", response_model=SkillSummaryDTO, status_code=status.HTTP_201_CREATED)
async def upload_skill_zip(
    file: UploadFile = File(...),
    skill_name: str | None = Form(default=None),
    overwrite: bool = Form(default=False),
) -> SkillSummaryDTO:
    """Upload and safely extract a `.zip` skill package into `src/skills/<name>/`."""
    raw = await file.read()
    try:
        summary = import_skill_zip(
            settings.resolved_skills_dir,
            raw,
            filename=file.filename or "skill.zip",
            skill_name=skill_name,
            overwrite=overwrite,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _summary_to_dto(summary)


@router.get("/{skill_name}", response_model=SkillSummaryDTO)
async def get_filesystem_skill(skill_name: str) -> SkillSummaryDTO:
    summary = get_skill_summary(settings.resolved_skills_dir, skill_name, include_tree=True)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill '{skill_name}' not found."
        )
    return _summary_to_dto(summary)


@router.delete("/{skill_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filesystem_skill(skill_name: str) -> None:
    try:
        delete_skill_dir(settings.resolved_skills_dir, skill_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{skill_name}/file", response_model=SkillFileContentDTO)
async def read_filesystem_skill_file(
    skill_name: str,
    path: str = Query(default="SKILL.md"),
) -> SkillFileContentDTO:
    try:
        res = read_skill_file(settings.resolved_skills_dir, skill_name, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (ValueError, IsADirectoryError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SkillFileContentDTO(**res)


@router.put("/{skill_name}/file", response_model=SkillFileContentDTO)
async def write_filesystem_skill_file(
    skill_name: str,
    payload: SkillFileWriteRequest,
) -> SkillFileContentDTO:
    try:
        res = write_skill_file(
            settings.resolved_skills_dir,
            skill_name,
            payload.path,
            payload.content,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (ValueError, IsADirectoryError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SkillFileContentDTO(**res)


@router.delete("/{skill_name}/file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filesystem_skill_file(
    skill_name: str,
    path: str = Query(..., min_length=1),
) -> None:
    try:
        delete_skill_file(settings.resolved_skills_dir, skill_name, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
