from __future__ import annotations

import re
from typing import Any

from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ScratchpadUpdateTool(BaseTool):
    name = "scratchpad_update"
    requires_sandbox = False
    description = (
        "Update the pinned Working Scratchpad with confirmed clues, discovered ports/endpoints, "
        "vulnerabilities, ruled-out paths, or structured notes. Entries here survive context "
        "compaction."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "add_clues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "New verified clues, credentials, offsets, or files.",
            },
            "add_endpoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Open ports, URLs, API endpoints, or services.",
            },
            "add_vulnerabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Confirmed or strongly suspected vulnerabilities.",
            },
            "add_ruled_out": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dead ends or failed hypotheses to avoid repeating.",
            },
            "notes_md": {
                "type": "string",
                "description": "Optional updated working notes in Markdown.",
            },
        },
    }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        ctx.scratchpad.merge_update(
            add_clues=arguments.get("add_clues"),
            add_endpoints=arguments.get("add_endpoints"),
            add_vulnerabilities=arguments.get("add_vulnerabilities"),
            add_ruled_out=arguments.get("add_ruled_out"),
            notes_md=arguments.get("notes_md"),
        )
        return ToolResult(
            success=True,
            output="Working scratchpad updated.\n\n" + ctx.scratchpad.to_prompt_block(),
            metadata={"scratchpad": ctx.scratchpad.model_dump()},
        )


class SubmitFlagTool(BaseTool):
    name = "submit_flag"
    requires_sandbox = False
    description = (
        "Submit a discovered CTF flag for verification. Call this immediately once you have "
        "recovered a valid flag matching the challenge's flag format."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "flag": {
                "type": "string",
                "description": "The exact flag string (e.g. flag{...}).",
            },
            "explanation": {
                "type": "string",
                "description": "Brief summary of how the flag was obtained.",
            },
        },
        "required": ["flag"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        flag = str(arguments.get("flag", "")).strip()
        explanation = str(arguments.get("explanation", "")).strip()
        if not flag:
            return ToolResult(success=False, error="Parameter 'flag' must be non-empty.")

        if ctx.flag_regex:
            if not re.fullmatch(ctx.flag_regex, flag):
                return ToolResult(
                    success=False,
                    output=f"Flag '{flag}' does not match expected format regex `{ctx.flag_regex}`.",
                    metadata={"flag": flag, "valid_format": False},
                )

        callback_res: dict[str, Any] = {"accepted": True, "status": "captured"}
        if ctx.on_flag_submitted is not None:
            callback_res = await ctx.on_flag_submitted(flag)

        accepted = bool(callback_res.get("accepted", True))
        return ToolResult(
            success=accepted,
            output=(
                f"Flag '{flag}' accepted ({callback_res.get('status', 'captured')})."
                if accepted
                else f"Flag '{flag}' rejected: {callback_res.get('message', 'incorrect')}."
            ),
            metadata={
                "flag": flag,
                "explanation": explanation,
                "accepted": accepted,
                "submission": callback_res,
            },
        )
