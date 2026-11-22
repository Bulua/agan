from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.engine.sandbox.path_guard import (
    IGNORED_WALK_DIRS,
    is_probably_binary,
    resolve_jailed_path,
)

INDEX_FILENAME = "MEMORY.md"
MAX_MEMORY_FILE_BYTES = 256 * 1024  # 256 KB per memory note
VALID_SCOPES = frozenset({"global", "agent", "shared"})
SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


@dataclass
class MemoryFileSummary:
    path: str  # POSIX relative path inside scope dir (e.g. "user-preferences.md")
    title: str
    memory_type: str
    summary: str
    tags: list[str] = field(default_factory=list)
    author: str = ""
    size: int = 0
    is_index: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "memory_type": self.memory_type,
            "summary": self.summary,
            "tags": list(self.tags),
            "author": self.author,
            "size": self.size,
            "is_index": self.is_index,
            "updated_at": self.updated_at.isoformat(),
        }


def validate_scope_id(scope_id: str) -> str:
    clean = str(scope_id or "").strip()
    if not clean or clean in (".", "..") or not SAFE_ID_RE.match(clean):
        raise ValueError(f"Invalid scope identifier '{scope_id}'.")
    return clean


def resolve_scope_dir(
    memories_root: Path,
    scope: str,
    *,
    scope_id: str | None = None,
    shared_dir: Path | None = None,
    create: bool = False,
) -> Path:
    """Resolve and strictly jail the host directory for a memory scope."""
    clean_scope = str(scope or "").strip().lower()
    if clean_scope not in VALID_SCOPES:
        raise ValueError("Scope must be one of: global, agent, shared.")

    root = Path(memories_root).resolve()

    if clean_scope == "global":
        target = (root / "global").resolve()
        if not target.is_relative_to(root):
            raise PermissionError("Invalid global memory directory.")
    elif clean_scope == "agent":
        if not scope_id:
            raise ValueError("Agent scope requires an agent identifier (`scope_id`).")
        clean_id = validate_scope_id(scope_id)
        agents_base = (root / "agents").resolve()
        target = (agents_base / clean_id).resolve()
        if not target.is_relative_to(agents_base):
            raise PermissionError("Invalid agent memory directory.")
    else:
        if shared_dir is None:
            raise ValueError("Shared blackboard directory is not configured for this run.")
        target = Path(shared_dir).resolve()

    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def parse_memory_frontmatter(raw_md: str, fallback_title: str) -> dict[str, Any]:
    """Parse optional YAML frontmatter + derive title, memory_type, tags, author, and summary."""
    text = (raw_md or "").lstrip("\ufeff")
    meta: dict[str, str] = {}
    body = text.strip()

    if body.startswith("---"):
        lines = body.splitlines()
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx != -1:
            for fl in lines[1:end_idx]:
                if ":" in fl:
                    k, v = fl.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip("\"'")
            body = "\n".join(lines[end_idx + 1 :]).strip()

    first_heading = ""
    first_para = ""
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# ") and not first_heading:
            first_heading = s[2:].strip()
            continue
        if not s.startswith("#") and not s.startswith("```") and not s.startswith("---"):
            first_para = s.lstrip("-* ").strip()
            break

    raw_tags = meta.get("tags", "")
    tags: list[str] = []
    if raw_tags:
        cleaned = raw_tags.strip("[]")
        tags = [t.strip().strip("\"'") for t in cleaned.split(",") if t.strip().strip("\"'")]

    title = meta.get("title") or first_heading or fallback_title
    memory_type = meta.get("type") or meta.get("category") or "note"
    author = meta.get("author") or ""
    summary = meta.get("summary") or meta.get("description") or first_para
    return {
        "title": title[:160],
        "memory_type": memory_type[:40],
        "tags": tags[:12],
        "author": author[:80],
        "summary": summary[:220],
        "body": body,
    }


def _normalize_memory_rel_path(raw_path: str) -> str:
    clean = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
    if not clean or clean.endswith("/"):
        raise ValueError("Memory file path must be a non-empty filename (e.g. 'preferences.md').")
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    return clean


def _compose_memory_markdown(
    content: str,
    *,
    title: str = "",
    memory_type: str = "",
    tags: list[str] | None = None,
    author: str = "",
) -> str:
    stripped = (content or "").lstrip("\ufeff").strip()
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if stripped.startswith("---"):
        lines = stripped.splitlines()
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx != -1:
            # Update `updated_at` and optionally fill missing `author`
            fm_lines = lines[1:end_idx]
            has_updated = False
            has_author = False
            new_fm: list[str] = []
            for fl in fm_lines:
                key = fl.split(":", 1)[0].strip().lower() if ":" in fl else ""
                if key == "updated_at":
                    new_fm.append(f"updated_at: {now_iso}")
                    has_updated = True
                elif key == "author":
                    new_fm.append(fl)
                    has_author = True
                else:
                    new_fm.append(fl)
            if author and not has_author:
                new_fm.append(f"author: {author}")
            if not has_updated:
                new_fm.append(f"updated_at: {now_iso}")
            body_rest = "\n".join(lines[end_idx + 1 :]).strip()
            return "---\n" + "\n".join(new_fm) + "\n---\n\n" + body_rest + "\n"

    # Build fresh clean frontmatter
    fm = ["---"]
    if title.strip():
        fm.append(f"title: {title.strip()}")
    if memory_type.strip():
        fm.append(f"type: {memory_type.strip()}")
    clean_tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if clean_tags:
        fm.append(f"tags: [{', '.join(clean_tags)}]")
    if author.strip():
        fm.append(f"author: {author.strip()}")
    fm.append(f"updated_at: {now_iso}")
    fm.append("---")
    return "\n".join(fm) + "\n\n" + stripped + "\n"


def list_memory_files(scope_dir: Path, *, include_index: bool = True) -> list[MemoryFileSummary]:
    """List all Markdown memory files inside `scope_dir` (MEMORY.md first, then sorted)."""
    base = Path(scope_dir).resolve()
    if not base.exists() or not base.is_dir():
        return []

    items: list[MemoryFileSummary] = []
    for dirpath, dirnames, filenames in os.walk(base):
        cur = Path(dirpath).resolve()
        if not cur.is_relative_to(base):
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORED_WALK_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if fn.startswith(".") or not fn.lower().endswith(".md"):
                continue
            fp = (cur / fn).resolve()
            if not fp.is_relative_to(base) or not fp.is_file():
                continue
            rel = fp.relative_to(base).as_posix()
            is_idx = rel.lower() == INDEX_FILENAME.lower()
            if is_idx and not include_index:
                continue
            try:
                st = fp.stat()
                raw = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_memory_frontmatter(raw, fallback_title=fp.stem)
            items.append(
                MemoryFileSummary(
                    path=rel,
                    title="Memory Index" if is_idx else parsed["title"],
                    memory_type="index" if is_idx else parsed["memory_type"],
                    summary=parsed["summary"],
                    tags=parsed["tags"],
                    author=parsed["author"],
                    size=st.st_size,
                    is_index=is_idx,
                    updated_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                )
            )

    items.sort(key=lambda x: (0 if x.is_index else 1, x.path.lower()))
    return items


def refresh_memory_index(scope_dir: Path, *, scope_label: str = "Memory") -> Path:
    """Regenerate `MEMORY.md` inside `scope_dir` from all non-index Markdown notes."""
    base = Path(scope_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    notes = list_memory_files(base, include_index=False)

    lines = [f"# {scope_label} Index", ""]
    if not notes:
        lines.append("_No memory notes recorded yet._")
    else:
        for item in notes[:120]:
            meta_bits = []
            if item.memory_type and item.memory_type != "note":
                meta_bits.append(f"`{item.memory_type}`")
            if item.author:
                meta_bits.append(f"by {item.author}")
            if item.tags:
                meta_bits.append(" ".join(f"#{t}" for t in item.tags[:4]))
            meta_str = f" ({', '.join(meta_bits)})" if meta_bits else ""
            summary_str = f" — {item.summary}" if item.summary else ""
            lines.append(f"- [{item.title}]({item.path}){meta_str}{summary_str}")
        if len(notes) > 120:
            lines.append(f"\n_... and {len(notes) - 120} more note(s). Use `memory_search` to query._")

    idx_path = base / INDEX_FILENAME
    idx_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return idx_path


def read_memory_file(scope_dir: Path, rel_path: str) -> dict[str, Any]:
    base = Path(scope_dir).resolve()
    cleaned = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        raise ValueError("Parameter 'path' is required.")

    host_path, _ = resolve_jailed_path(
        cleaned,
        base,
        virtual_cwd="/memory",
        virtual_root="/memory",
    )
    if not host_path.exists() and not cleaned.lower().endswith(".md"):
        alt, _ = resolve_jailed_path(
            f"{cleaned}.md",
            base,
            virtual_cwd="/memory",
            virtual_root="/memory",
        )
        if alt.exists():
            host_path = alt

    if not host_path.exists():
        raise FileNotFoundError(f"Memory file '{cleaned}' not found.")
    if not host_path.is_file():
        raise IsADirectoryError(f"Path '{cleaned}' is a directory.")

    raw = host_path.read_bytes()
    if is_probably_binary(raw):
        raise ValueError(f"Memory file '{cleaned}' appears to be binary.")

    text = raw.decode("utf-8", errors="replace")
    rel_norm = host_path.relative_to(base).as_posix()
    parsed = parse_memory_frontmatter(text, fallback_title=host_path.stem)
    st = host_path.stat()
    return {
        "path": rel_norm,
        "content": text,
        "title": parsed["title"],
        "memory_type": parsed["memory_type"],
        "tags": parsed["tags"],
        "author": parsed["author"],
        "summary": parsed["summary"],
        "size": st.st_size,
        "is_index": rel_norm.lower() == INDEX_FILENAME.lower(),
        "updated_at": datetime.fromtimestamp(st.st_mtime, tz=UTC),
    }


def write_memory_file(
    scope_dir: Path,
    rel_path: str,
    content: str,
    *,
    title: str = "",
    memory_type: str = "",
    tags: list[str] | None = None,
    author: str = "",
    raw_mode: bool = False,
    scope_label: str = "Memory",
) -> dict[str, Any]:
    """Create or overwrite a Markdown memory file and automatically refresh `MEMORY.md`."""
    base = Path(scope_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    norm_rel = _normalize_memory_rel_path(rel_path)

    host_path, _ = resolve_jailed_path(
        norm_rel,
        base,
        virtual_cwd="/memory",
        virtual_root="/memory",
    )
    if host_path == base or (host_path.exists() and host_path.is_dir()):
        raise IsADirectoryError(f"Path '{norm_rel}' is a directory.")

    is_index = host_path.relative_to(base).as_posix().lower() == INDEX_FILENAME.lower()
    final_text = (
        content
        if (raw_mode or is_index)
        else _compose_memory_markdown(
            content,
            title=title or host_path.stem.replace("-", " ").replace("_", " ").title(),
            memory_type=memory_type,
            tags=tags,
            author=author,
        )
    )
    payload = final_text.encode("utf-8")
    if len(payload) > MAX_MEMORY_FILE_BYTES:
        raise ValueError(f"Memory file exceeds {MAX_MEMORY_FILE_BYTES // 1024} KB limit.")

    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(final_text, encoding="utf-8", newline="\n")

    if not is_index:
        refresh_memory_index(base, scope_label=scope_label)

    return read_memory_file(base, host_path.relative_to(base).as_posix())


def edit_memory_file(
    scope_dir: Path,
    rel_path: str,
    old_text: str,
    new_text: str,
    *,
    replace_all: bool = False,
    scope_label: str = "Memory",
) -> dict[str, Any]:
    """Apply a targeted exact-string replacement on an existing memory file and refresh index."""
    if not old_text:
        raise ValueError("Parameter 'old_text' must be non-empty.")

    base = Path(scope_dir).resolve()
    existing = read_memory_file(base, rel_path)
    original = existing["content"]

    match_target = old_text
    match_replacement = new_text
    if "\r\n" in original and "\r\n" not in old_text and "\n" in old_text:
        if original.replace("\r\n", "\n").count(old_text) > 0:
            match_target = old_text.replace("\n", "\r\n")
            match_replacement = new_text.replace("\r\n", "\n").replace("\n", "\r\n")

    occurrences = original.count(match_target)
    if occurrences == 0:
        raise ValueError(
            f"Target `old_text` was not found in '{existing['path']}'. "
            "Call `memory_read` first to verify exact text."
        )
    if occurrences > 1 and not replace_all:
        raise ValueError(
            f"Target `old_text` matched {occurrences} times in '{existing['path']}'. "
            "Include more surrounding context or pass `replace_all=true`."
        )

    updated = (
        original.replace(match_target, match_replacement)
        if replace_all
        else original.replace(match_target, match_replacement, 1)
    )
    replacements = occurrences if replace_all else 1
    written = write_memory_file(
        base,
        existing["path"],
        updated,
        raw_mode=True,
        scope_label=scope_label,
    )
    old_lines = len(old_text.splitlines()) or 1
    new_lines = len(new_text.splitlines()) if new_text else 0
    written["replacements"] = replacements
    written["lines_added"] = new_lines * replacements
    written["lines_removed"] = old_lines * replacements
    return written


def delete_memory_file(
    scope_dir: Path,
    rel_path: str,
    *,
    scope_label: str = "Memory",
) -> str:
    base = Path(scope_dir).resolve()
    cleaned = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        raise ValueError("Parameter 'path' is required.")

    host_path, _ = resolve_jailed_path(
        cleaned,
        base,
        virtual_cwd="/memory",
        virtual_root="/memory",
    )
    if not host_path.exists() and not cleaned.lower().endswith(".md"):
        alt, _ = resolve_jailed_path(
            f"{cleaned}.md",
            base,
            virtual_cwd="/memory",
            virtual_root="/memory",
        )
        if alt.exists():
            host_path = alt

    if host_path == base:
        raise PermissionError("Cannot delete memory scope root directory.")
    if not host_path.exists():
        raise FileNotFoundError(f"Memory file '{cleaned}' not found.")
    if host_path.is_dir():
        raise IsADirectoryError(f"Path '{cleaned}' is a directory.")

    rel_norm = host_path.relative_to(base).as_posix()
    host_path.unlink()

    # Clean empty subdirectories
    parent = host_path.parent
    while parent != base and parent.is_relative_to(base):
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
        except OSError:
            break

    if rel_norm.lower() != INDEX_FILENAME.lower():
        refresh_memory_index(base, scope_label=scope_label)
    return rel_norm


def search_memory_files(
    scope_dirs: list[tuple[str, Path]],
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search across one or more `(scope_name, scope_dir)` pairs by tokens/substring."""
    q = (query or "").strip().lower()
    if not q:
        return []
    terms = [t for t in re.split(r"\s+", q) if t]
    cap = max(1, min(int(limit or 10), 50))

    scored: list[tuple[int, dict[str, Any]]] = []
    for scope_name, sdir in scope_dirs:
        if not sdir.exists() or not sdir.is_dir():
            continue
        for item in list_memory_files(sdir, include_index=False):
            try:
                raw = (sdir / item.path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hay_meta = f"{item.path} {item.title} {item.memory_type} {' '.join(item.tags)}".lower()
            raw_lower = raw.lower()

            score = 0
            if q in hay_meta:
                score += 10
            if q in raw_lower:
                score += 5
            for term in terms:
                if term in hay_meta:
                    score += 4
                if term in raw_lower:
                    score += 2
            if score == 0:
                continue

            # Extract first matching line snippet
            snippet = item.summary
            for ln_no, line in enumerate(raw.splitlines(), start=1):
                ll = line.lower()
                if q in ll or any(t in ll for t in terms):
                    snippet = f"L{ln_no}: {line.strip()[:220]}"
                    break

            scored.append(
                (
                    score,
                    {
                        "scope": scope_name,
                        "path": item.path,
                        "title": item.title,
                        "memory_type": item.memory_type,
                        "tags": item.tags,
                        "author": item.author,
                        "snippet": snippet,
                        "score": score,
                    },
                )
            )

    scored.sort(key=lambda pair: (-pair[0], pair[1]["scope"], pair[1]["path"]))
    return [entry for _, entry in scored[:cap]]


def build_shared_blackboard_pin(shared_dir: Path | None, *, max_chars: int = 2400) -> str:
    """Build a compact pinned Markdown summary of the `shared` multi-agent blackboard.

    Injected into System Prompt AND passed into `ContextCompactor.compact_with_stats(pinned_blocks=...)`
    so critical cross-agent facts, findings, and handoffs survive context compaction.
    """
    if shared_dir is None or not shared_dir.exists():
        return ""
    notes = list_memory_files(shared_dir, include_index=False)
    if not notes:
        return ""

    lines = ["## Shared Task Blackboard (`scope=\"shared\"` — Visible to All Agents)"]
    used = len(lines[0])
    for item in notes[:20]:
        author_tag = f" [by {item.author}]" if item.author else ""
        entry = f"- `{item.path}` ({item.title}{author_tag}): {item.summary}"
        if used + len(entry) > max_chars:
            lines.append("- ... (use `memory_search(scope='shared')` or `memory_read` for more)")
            break
        lines.append(entry)
        used += len(entry) + 1
    return "\n".join(lines)


def build_memory_prompt_block(
    *,
    global_dir: Path | None,
    agent_dir: Path | None,
    shared_dir: Path | None,
    agent_name: str,
    memory_mode: str = "read_write",
    max_items_per_scope: int = 25,
) -> str:
    """Build a compact Claude-style `<memory_context>` index block for System Prompt injection."""
    sections: list[str] = ["<memory_context>"]

    writable_hint = (
        "You have read-only access to long-term (`global`, `agent`) memory, while `shared` task "
        "blackboard remains writable for multi-agent handoffs."
        if memory_mode == "read_only"
        else (
            "Use `memory_read` / `memory_search` to load full details on demand. "
            "Use `memory_write` / `memory_edit` to save durable knowledge:\n"
            "- `scope=\"global\"`: user-wide preferences, general rules shared across all agents.\n"
            "- `scope=\"agent\"`: long-term lessons & heuristics specific to this agent.\n"
            "- `scope=\"shared\"`: current task blackboard shared with parent/subagents "
            "(verified facts, file artifacts, endpoints, handoffs, ruled-out directions — "
            "preserved across context compaction).\n"
            "Do NOT store transient raw command outputs or one-off scratch text in `global` or `agent` memory."
        )
    )
    sections.append(writable_hint)

    shared_pin = build_shared_blackboard_pin(shared_dir)
    if shared_pin:
        sections.append(shared_pin)

    if agent_dir is not None and agent_dir.exists():
        agent_notes = list_memory_files(agent_dir, include_index=False)[:max_items_per_scope]
        if agent_notes:
            lines = [f"### Agent Memory Index (`scope=\"agent\"`, {agent_name})"]
            for item in agent_notes:
                tag_str = f" [{', '.join(item.tags[:3])}]" if item.tags else ""
                lines.append(f"- `{item.path}` — **{item.title}**{tag_str}: {item.summary}")
            sections.append("\n".join(lines))

    if global_dir is not None and global_dir.exists():
        global_notes = list_memory_files(global_dir, include_index=False)[:max_items_per_scope]
        if global_notes:
            lines = ["### Global Memory Index (`scope=\"global\"`)"]
            for item in global_notes:
                tag_str = f" [{', '.join(item.tags[:3])}]" if item.tags else ""
                lines.append(f"- `{item.path}` — **{item.title}**{tag_str}: {item.summary}")
            sections.append("\n".join(lines))

    sections.append("</memory_context>")
    return "\n\n".join(sections)
