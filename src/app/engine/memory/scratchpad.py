from __future__ import annotations

from pydantic import BaseModel, Field


class WorkingScratchpad(BaseModel):
    """Short-term working memory pinned across context compactions.

    Retains verified clues, open endpoints/ports, discovered vulnerabilities,
    ruled-out paths, and freeform notes so the Agent never forgets critical
    findings during long CTF sessions.
    """

    known_clues: list[str] = Field(default_factory=list)
    endpoints_ports: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    notes_md: str = ""

    def merge_update(
        self,
        *,
        add_clues: list[str] | None = None,
        add_endpoints: list[str] | None = None,
        add_vulnerabilities: list[str] | None = None,
        add_ruled_out: list[str] | None = None,
        notes_md: str | None = None,
    ) -> None:
        """Append unique entries and optionally update notes."""
        for item in add_clues or []:
            if item and item not in self.known_clues:
                self.known_clues.append(item)
        for item in add_endpoints or []:
            if item and item not in self.endpoints_ports:
                self.endpoints_ports.append(item)
        for item in add_vulnerabilities or []:
            if item and item not in self.vulnerabilities:
                self.vulnerabilities.append(item)
        for item in add_ruled_out or []:
            if item and item not in self.ruled_out:
                self.ruled_out.append(item)
        if notes_md is not None:
            self.notes_md = notes_md

    def is_empty(self) -> bool:
        return not (
            self.known_clues
            or self.endpoints_ports
            or self.vulnerabilities
            or self.ruled_out
            or self.notes_md.strip()
        )

    def to_prompt_block(self) -> str:
        """Render a compact pinned Markdown block for LLM context injection."""
        if self.is_empty():
            return "## Working Scratchpad (Pinned)\n(No findings recorded yet.)"

        sections = ["## Working Scratchpad (Pinned — Always Preserved)"]
        if self.endpoints_ports:
            sections.append(
                "### Endpoints / Ports / Services\n"
                + "\n".join(f"- {x}" for x in self.endpoints_ports)
            )
        if self.known_clues:
            sections.append(
                "### Known Clues & Artifacts\n"
                + "\n".join(f"- {x}" for x in self.known_clues)
            )
        if self.vulnerabilities:
            sections.append(
                "### Confirmed / Suspected Vulnerabilities\n"
                + "\n".join(f"- {x}" for x in self.vulnerabilities)
            )
        if self.ruled_out:
            sections.append(
                "### Ruled-Out Directions (Do NOT Repeat)\n"
                + "\n".join(f"- {x}" for x in self.ruled_out)
            )
        if self.notes_md.strip():
            sections.append(f"### Working Notes\n{self.notes_md.strip()}")
        return "\n\n".join(sections)
