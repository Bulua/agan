from __future__ import annotations

from pathlib import Path
from typing import Any

from app.engine.skills.fs_loader import (
    SkillSummary,
    get_skill_summary,
    list_skill_relative_files,
    read_skill_file,
)
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult
from app.engine.tools.registry import ToolRegistry


class LoadSkillTool(BaseTool):
    """Progressive 3-stage disclosure tool for filesystem-backed Skill packages.

    - Stage 2: `load_skill(name)` loads `SKILL.md` plus the list of accompanying files
      inside `skills/<name>/` (scripts, references, templates, resources).
    - Stage 3: `load_skill(name, file_path="scripts/...")` reads a specific accompanying
      file inside that skill package on demand.
    """

    name = "load_skill"
    requires_sandbox = False

    def __init__(self, skills: list[SkillSummary], skills_root: Path) -> None:
        self._skills_root = Path(skills_root).resolve()
        self._skills_map: dict[str, SkillSummary] = {s.name: s for s in skills}
        skill_names = sorted(self._skills_map.keys())
        self.description = (
            "Load a bound skill package's `SKILL.md` playbook (and see its bundled scripts/references), "
            "or pass `file_path` to read a specific bundled file inside that skill directory."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact folder name of the bound skill to load.",
                    **({"enum": skill_names} if skill_names else {}),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Optional relative path inside the skill folder (e.g. 'references/checklist.md' "
                        "or 'scripts/helper.py'). Omit to load the main SKILL.md and file listing."
                    ),
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        req_name = str(arguments.get("name", "")).strip()
        if not req_name:
            return ToolResult(success=False, error="Parameter 'name' is required.")
        if req_name not in self._skills_map:
            available = ", ".join(sorted(self._skills_map.keys()))
            return ToolResult(
                success=False,
                error=f"Skill '{req_name}' is not bound to this Agent. Available skills: {available}",
            )

        rel_file = str(arguments.get("file_path") or "").strip()

        # Stage 3: Read a specific file inside the skill package
        if rel_file:
            try:
                res = read_skill_file(self._skills_root, req_name, rel_file)
            except (FileNotFoundError, PermissionError, ValueError, IsADirectoryError) as exc:
                return ToolResult(success=False, error=str(exc))

            if res["is_binary"]:
                return ToolResult(
                    success=True,
                    output=(
                        f"File `{req_name}/{res['path']}` ({res['size']} bytes) is binary. "
                        f"In Docker sandbox mode, access it read-only at `/opt/agan/skills/{req_name}/{res['path']}`."
                    ),
                    metadata={
                        "skill_name": req_name,
                        "file_path": res["path"],
                        "is_binary": True,
                        "bytes": res["size"],
                    },
                )

            header = f"# Skill Resource: `{req_name}/{res['path']}`\n\n"
            body = res["content"]
            if res["truncated"]:
                body += "\n\n... [truncated at 512 KB]"
            return ToolResult(
                success=True,
                output=header + body,
                metadata={
                    "skill_name": req_name,
                    "file_path": res["path"],
                    "bytes": res["size"],
                    "truncated": res["truncated"],
                },
            )

        # Stage 2: Load SKILL.md + bundled file manifest
        all_files = list_skill_relative_files(self._skills_root, req_name)
        skill_md_rel = next((p for p in all_files if p.lower() == "skill.md"), None)
        extra_files = [p for p in all_files if p != skill_md_rel]

        if skill_md_rel:
            try:
                md_res = read_skill_file(self._skills_root, req_name, skill_md_rel)
                body = md_res["content"].strip() or "(empty SKILL.md)"
            except Exception as exc:
                body = f"(failed to read SKILL.md: {exc})"
        else:
            body = "(No SKILL.md found in this skill directory.)"

        parts = [f"# Skill: `{req_name}`", "", body]
        if extra_files:
            parts.extend(
                [
                    "",
                    "---",
                    "## Bundled Files in This Skill Package",
                    "Call `load_skill(name=\""
                    + req_name
                    + "\", file_path=\"<path>\")` to inspect any file below, "
                    "or execute/read them directly in a Docker sandbox under "
                    f"`/opt/agan/skills/{req_name}/`:",
                ]
            )
            for fp in extra_files:
                parts.append(f"- `{fp}`")

        return ToolResult(
            success=True,
            output="\n".join(parts),
            metadata={
                "skill_name": req_name,
                "file_path": skill_md_rel or "",
                "bundled_files": extra_files,
                "chars": len(body),
            },
        )


def attach_skills_to_agent_run(
    skill_names: list[str],
    skills_root: Path,
    tool_registry: ToolRegistry,
) -> str:
    """Attach bound filesystem skills via compact `<available_skills>` index + `load_skill` tool."""
    if not skill_names:
        return ""

    resolved: list[SkillSummary] = []
    seen: set[str] = set()
    for raw_name in skill_names:
        clean = str(raw_name or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        summary = get_skill_summary(skills_root, clean, include_tree=False)
        if summary is not None:
            resolved.append(summary)

    if not resolved:
        return ""

    tool_registry.register(LoadSkillTool(resolved, skills_root), enabled=True)

    lines = [
        "## Available Skills",
        "When a task matches one of the skills below, call `load_skill(name=\"<skill>\")` to load "
        "its `SKILL.md` playbook and see any bundled scripts or references. You can then call "
        "`load_skill(name=\"<skill>\", file_path=\"<relative_path>\")` to read any bundled file.",
        "<available_skills>",
    ]
    for s in resolved:
        desc = (s.description or s.title).strip()
        files_hint = f" ({s.file_count} files)" if s.file_count > 1 else ""
        lines.append(f"- `{s.name}`{files_hint}: {desc}")
    lines.append("</available_skills>")
    return "\n".join(lines)
