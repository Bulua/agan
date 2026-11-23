from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import settings
from app.engine.skills.fs_loader import (
    create_skill_dir,
    delete_skill_file,
    list_skill_relative_files,
    list_skills,
    read_skill_file,
    write_skill_file,
)
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult


def _build_skill_md(
    name: str,
    title: str,
    description: str,
    body: str,
) -> str:
    stripped = (body or "").lstrip("\ufeff").strip()
    if stripped.startswith("---"):
        return stripped + "\n"

    fm_lines = ["---", f"name: {name}"]
    if title.strip():
        fm_lines.append(f"title: {title.strip()}")
    if description.strip():
        fm_lines.append(f"description: {description.strip()}")
    fm_lines.append("---")

    if not stripped:
        heading = title.strip() or name
        desc_line = description.strip() or f"Instructions and workflow for {name}."
        stripped = f"# {heading}\n\n{desc_line}\n"

    return "\n".join(fm_lines) + "\n\n" + stripped + "\n"


class CreateSkillTool(BaseTool):
    name = "create_skill"
    requires_sandbox = False
    description = (
        "Create a new reusable Skill package folder on disk containing `SKILL.md` and optional "
        "bundled scripts, references, or templates. Immediately appears in Studio -> Skills."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Folder slug for the new skill (1-80 chars, letters/digits/'.'/'_'/'-', "
                    "e.g. 'code-review-guide' or 'api-debugging')."
                ),
            },
            "title": {
                "type": "string",
                "description": "Optional human-readable title for YAML frontmatter.",
            },
            "description": {
                "type": "string",
                "description": "Short 1-2 sentence summary shown in the Agent's skill index.",
            },
            "skill_md_content": {
                "type": "string",
                "description": "Full Markdown instructions for SKILL.md (frontmatter added automatically if omitted).",
            },
            "files": {
                "type": "array",
                "description": "Optional extra bundled files to create inside the skill folder.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path inside the skill folder (e.g. 'scripts/helper.py').",
                        },
                        "content": {
                            "type": "string",
                            "description": "UTF-8 file content.",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        "required": ["name", "skill_md_content"],
    }

    def __init__(self, skills_root: Path | None = None) -> None:
        self._skills_root = skills_root

    def _root(self) -> Path:
        return (self._skills_root or settings.resolved_skills_dir).resolve()

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        name = str(arguments.get("name") or "").strip()
        title = str(arguments.get("title") or "").strip()
        description = str(arguments.get("description") or "").strip()
        raw_md = str(arguments.get("skill_md_content") or "")
        extra_files = arguments.get("files") or []

        if not name:
            return ToolResult(success=False, error="Parameter 'name' is required.")
        if not raw_md.strip():
            return ToolResult(success=False, error="Parameter 'skill_md_content' must be non-empty.")

        root = self._root()
        md_content = _build_skill_md(name, title, description, raw_md)

        try:
            summary = create_skill_dir(
                root,
                name,
                description=description,
                content_md=md_content,
            )
            created_extra: list[str] = []
            if isinstance(extra_files, list):
                for entry in extra_files:
                    if not isinstance(entry, dict):
                        continue
                    rel_path = str(entry.get("path") or "").strip()
                    file_content = str(entry.get("content") or "")
                    if not rel_path or rel_path.lower() == "skill.md":
                        continue
                    written = write_skill_file(root, summary.name, rel_path, file_content)
                    created_extra.append(written["path"])
        except (ValueError, FileExistsError, PermissionError, IsADirectoryError) as exc:
            return ToolResult(success=False, error=str(exc))

        all_files = list_skill_relative_files(root, summary.name)
        lines = [
            f"Created skill `{summary.name}` ({len(all_files)} file(s)):",
            *[f"- `{fp}`" for fp in all_files],
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "skill_name": summary.name,
                "files": all_files,
                "created_extra": created_extra,
            },
        )


class EditSkillFileTool(BaseTool):
    name = "edit_skill_file"
    requires_sandbox = False
    description = (
        "Inspect, create, edit, or delete files inside Skill packages on disk. "
        "Supports actions: `list` (list skills or files in a skill), `read`, `write`, "
        "`edit` (exact text replacement), and `delete`."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "write", "edit", "delete"],
                "description": "Operation to perform on skill packages.",
            },
            "skill_name": {
                "type": "string",
                "description": "Target skill folder name. Optional only when `action='list'` to list all skills.",
            },
            "file_path": {
                "type": "string",
                "description": "Relative file path inside the skill folder (e.g. 'SKILL.md', 'scripts/run.py').",
            },
            "content": {
                "type": "string",
                "description": "Full UTF-8 content to write (required for `action='write'`).",
            },
            "old_text": {
                "type": "string",
                "description": "Exact text block to replace (required for `action='edit'`).",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text (required for `action='edit'`).",
            },
            "replace_all": {
                "type": "boolean",
                "description": "For `action='edit'`, replace all occurrences instead of requiring a unique match.",
                "default": False,
            },
        },
        "required": ["action"],
    }

    def __init__(self, skills_root: Path | None = None) -> None:
        self._skills_root = skills_root

    def _root(self) -> Path:
        return (self._skills_root or settings.resolved_skills_dir).resolve()

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        action = str(arguments.get("action") or "").strip().lower()
        skill_name = str(arguments.get("skill_name") or "").strip()
        file_path = str(arguments.get("file_path") or "").strip()
        root = self._root()

        if action not in ("list", "read", "write", "edit", "delete"):
            return ToolResult(
                success=False,
                error="Parameter 'action' must be one of: list, read, write, edit, delete.",
            )

        try:
            if action == "list":
                if not skill_name:
                    skills = list_skills(root)
                    if not skills:
                        return ToolResult(
                            success=True,
                            output="No skills exist yet in the skills directory.",
                            metadata={"action": "list", "count": 0},
                        )
                    lines = [f"Available skills on disk ({len(skills)}):"]
                    for s in skills:
                        desc = f" — {s.description[:120]}" if s.description else ""
                        lines.append(f"- `{s.name}` ({s.file_count} file(s)){desc}")
                    return ToolResult(
                        success=True,
                        output="\n".join(lines),
                        metadata={"action": "list", "count": len(skills)},
                    )
                files = list_skill_relative_files(root, skill_name)
                if not files:
                    return ToolResult(
                        success=False,
                        error=f"Skill '{skill_name}' not found or contains no files.",
                    )
                lines = [f"Files in skill `{skill_name}` ({len(files)}):"] + [
                    f"- `{fp}`" for fp in files
                ]
                return ToolResult(
                    success=True,
                    output="\n".join(lines),
                    metadata={"action": "list", "skill_name": skill_name, "files": files},
                )

            if not skill_name:
                return ToolResult(success=False, error=f"Parameter 'skill_name' is required for action '{action}'.")
            if not file_path:
                return ToolResult(success=False, error=f"Parameter 'file_path' is required for action '{action}'.")

            if action == "read":
                res = read_skill_file(root, skill_name, file_path)
                if res["is_binary"]:
                    return ToolResult(
                        success=False,
                        error=f"File `{skill_name}/{res['path']}` ({res['size']} B) is binary and cannot be read as text.",
                    )
                body = res["content"]
                if res["truncated"]:
                    body += "\n\n... [truncated]"
                return ToolResult(
                    success=True,
                    output=body,
                    metadata={
                        "action": "read",
                        "skill_name": res["skill_name"],
                        "file_path": res["path"],
                        "bytes": res["size"],
                    },
                )

            if action == "write":
                content = str(arguments.get("content") or "")
                res = write_skill_file(root, skill_name, file_path, content)
                return ToolResult(
                    success=True,
                    output=f"Wrote `{res['skill_name']}/{res['path']}` ({res['size']} bytes).",
                    metadata={
                        "action": "write",
                        "skill_name": res["skill_name"],
                        "file_path": res["path"],
                        "bytes": res["size"],
                    },
                )

            if action == "edit":
                old_text = str(arguments.get("old_text") or "")
                new_text = str(arguments.get("new_text") or "")
                replace_all = bool(arguments.get("replace_all", False))
                if not old_text:
                    return ToolResult(success=False, error="Parameter 'old_text' is required for action 'edit'.")
                existing = read_skill_file(root, skill_name, file_path)
                if existing["is_binary"]:
                    return ToolResult(
                        success=False,
                        error=f"File `{skill_name}/{existing['path']}` is binary and cannot be edited.",
                    )
                original = existing["content"]
                match_target = old_text
                match_replacement = new_text
                if "\r\n" in original and "\r\n" not in old_text and "\n" in old_text:
                    if original.replace("\r\n", "\n").count(old_text) > 0:
                        match_target = old_text.replace("\n", "\r\n")
                        match_replacement = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
                occurrences = original.count(match_target)
                if occurrences == 0:
                    return ToolResult(
                        success=False,
                        error=f"Target `old_text` not found in `{skill_name}/{existing['path']}`.",
                    )
                if occurrences > 1 and not replace_all:
                    return ToolResult(
                        success=False,
                        error=(
                            f"Target `old_text` matched {occurrences} times in `{skill_name}/{existing['path']}`. "
                            "Include more surrounding lines or pass `replace_all=true`."
                        ),
                    )
                updated = (
                    original.replace(match_target, match_replacement)
                    if replace_all
                    else original.replace(match_target, match_replacement, 1)
                )
                replacements = occurrences if replace_all else 1
                res = write_skill_file(root, skill_name, existing["path"], updated)
                old_lines = len(old_text.splitlines()) or 1
                new_lines = len(new_text.splitlines()) if new_text else 0
                return ToolResult(
                    success=True,
                    output=(
                        f"Edited `{res['skill_name']}/{res['path']}`: {replacements} replacement(s) "
                        f"(+{new_lines * replacements} -{old_lines * replacements} lines)."
                    ),
                    metadata={
                        "action": "edit",
                        "skill_name": res["skill_name"],
                        "file_path": res["path"],
                        "replacements": replacements,
                        "lines_added": new_lines * replacements,
                        "lines_removed": old_lines * replacements,
                    },
                )

            # delete
            delete_skill_file(root, skill_name, file_path)
            return ToolResult(
                success=True,
                output=f"Deleted `{skill_name}/{file_path}`.",
                metadata={"action": "delete", "skill_name": skill_name, "file_path": file_path},
            )
        except (FileNotFoundError, PermissionError, ValueError, IsADirectoryError) as exc:
            return ToolResult(success=False, error=str(exc))
