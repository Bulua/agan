from __future__ import annotations

import io
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.engine.sandbox.path_guard import is_probably_binary, resolve_jailed_path

IGNORED_NAMES = {
    "__pycache__",
    ".DS_Store",
    "Thumbs.db",
    ".git",
    ".svn",
    ".hg",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
}

SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
MAX_READ_BYTES = 512 * 1024  # 512 KB for viewing/loading text files
MAX_WRITE_BYTES = 512 * 1024  # 512 KB per edited text file
MAX_ZIP_BYTES = 20 * 1024 * 1024  # 20 MB upload cap
MAX_ZIP_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024  # 50 MB zip-bomb guard
MAX_ZIP_FILES = 300


@dataclass
class SkillTreeNode:
    name: str
    path: str  # POSIX relative path inside the skill folder (e.g. "scripts/rsa_quick_check.py")
    kind: str  # "dir" | "file"
    size: int = 0
    children: list[SkillTreeNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class SkillSummary:
    name: str
    title: str
    description: str
    has_skill_md: bool
    file_count: int
    total_size: int
    updated_at: datetime
    tree: list[SkillTreeNode] = field(default_factory=list)


def validate_skill_name(name: str) -> str:
    clean = str(name or "").strip()
    if not clean or clean in (".", "..") or not SKILL_NAME_RE.match(clean):
        raise ValueError(
            "Invalid skill folder name. Use letters, digits, '.', '_' or '-' (1-80 chars, starting with a letter or digit)."
        )
    return clean


def slugify_skill_name(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(text or "").strip().lower()).strip("-._")
    if not slug or not slug[0].isalnum():
        slug = f"skill-{slug or 'custom'}"
    return slug[:80]


def parse_skill_md_meta(raw_md: str, fallback_name: str) -> tuple[str, str]:
    """Extract (title, description) from optional YAML frontmatter or Markdown body."""
    text = (raw_md or "").lstrip("\ufeff").strip()
    if not text:
        return fallback_name, ""

    meta: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        lines = text.splitlines()
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
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            first_heading = s[2:].strip()
            break

    title = meta.get("display_name") or meta.get("title") or first_heading or fallback_name
    desc = meta.get("description") or ""
    if not desc:
        for line in body.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("```") and not s.startswith("---"):
                desc = s[:240]
                break

    return title[:160], desc[:500]


def _resolve_skill_dir(root: Path, skill_name: str, *, must_exist: bool = True) -> Path:
    clean = validate_skill_name(skill_name)
    root_res = Path(root).resolve()
    root_res.mkdir(parents=True, exist_ok=True)
    target = (root_res / clean).resolve()
    if not target.is_relative_to(root_res) or target == root_res:
        raise PermissionError(f"Access denied: invalid skill directory '{skill_name}'.")
    if must_exist and (not target.exists() or not target.is_dir()):
        raise FileNotFoundError(f"Skill '{clean}' not found in skills directory.")
    return target


def _build_tree(skill_dir: Path, current: Path) -> tuple[list[SkillTreeNode], int, int, float]:
    """Recursively build sorted directory tree. Returns (nodes, file_count, total_size, max_mtime)."""
    entries: list[Path] = []
    try:
        for p in current.iterdir():
            if p.name in IGNORED_NAMES or p.name.startswith("."):
                continue
            entries.append(p)
    except OSError:
        return [], 0, 0, 0.0

    # Sort: SKILL.md first at root, then directories alphabetically, then files alphabetically
    def sort_key(p: Path) -> tuple[int, int, str]:
        is_root_skill_md = 0 if (current == skill_dir and p.is_file() and p.name.upper() == "SKILL.MD") else 1
        is_file = 1 if p.is_file() else 0
        return (is_root_skill_md, is_file, p.name.lower())

    entries.sort(key=sort_key)

    nodes: list[SkillTreeNode] = []
    file_count = 0
    total_size = 0
    max_mtime = 0.0

    for entry in entries:
        rel_posix = entry.relative_to(skill_dir).as_posix()
        try:
            st = entry.stat()
            max_mtime = max(max_mtime, st.st_mtime)
        except OSError:
            continue

        if entry.is_dir():
            children, sub_cnt, sub_size, sub_mtime = _build_tree(skill_dir, entry)
            file_count += sub_cnt
            total_size += sub_size
            max_mtime = max(max_mtime, sub_mtime)
            nodes.append(
                SkillTreeNode(
                    name=entry.name,
                    path=rel_posix,
                    kind="dir",
                    size=sub_size,
                    children=children,
                )
            )
        elif entry.is_file():
            file_count += 1
            total_size += st.st_size
            nodes.append(
                SkillTreeNode(
                    name=entry.name,
                    path=rel_posix,
                    kind="file",
                    size=st.st_size,
                    children=[],
                )
            )

    return nodes, file_count, total_size, max_mtime


def _find_skill_md(skill_dir: Path) -> Path | None:
    direct = skill_dir / "SKILL.md"
    if direct.is_file():
        return direct
    for p in skill_dir.iterdir():
        if p.is_file() and p.name.lower() == "skill.md":
            return p
    return None


def get_skill_summary(root: Path, skill_name: str, *, include_tree: bool = True) -> SkillSummary | None:
    try:
        skill_dir = _resolve_skill_dir(root, skill_name, must_exist=True)
    except (FileNotFoundError, ValueError, PermissionError):
        return None

    tree, file_count, total_size, max_mtime = _build_tree(skill_dir, skill_dir)
    if max_mtime <= 0:
        try:
            max_mtime = skill_dir.stat().st_mtime
        except OSError:
            max_mtime = datetime.now(UTC).timestamp()

    skill_md_path = _find_skill_md(skill_dir)
    has_skill_md = skill_md_path is not None
    title = skill_dir.name
    description = ""
    if skill_md_path is not None:
        try:
            raw = skill_md_path.read_text(encoding="utf-8", errors="replace")
            title, description = parse_skill_md_meta(raw, skill_dir.name)
        except OSError:
            pass

    return SkillSummary(
        name=skill_dir.name,
        title=title,
        description=description,
        has_skill_md=has_skill_md,
        file_count=file_count,
        total_size=total_size,
        updated_at=datetime.fromtimestamp(max_mtime, tz=UTC),
        tree=tree if include_tree else [],
    )


def list_skills(root: Path, *, search: str | None = None) -> list[SkillSummary]:
    root_res = Path(root).resolve()
    root_res.mkdir(parents=True, exist_ok=True)
    q = (search or "").strip().lower()

    results: list[SkillSummary] = []
    for entry in sorted(root_res.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name in IGNORED_NAMES or entry.name.startswith("."):
            continue
        summary = get_skill_summary(root_res, entry.name, include_tree=True)
        if summary is None:
            continue
        if q:
            hay = f"{summary.name} {summary.title} {summary.description}".lower()
            if q not in hay:
                continue
        results.append(summary)
    return results


def list_skill_relative_files(root: Path, skill_name: str) -> list[str]:
    """Return a sorted flat list of all relative file paths inside a skill folder."""
    summary = get_skill_summary(root, skill_name, include_tree=True)
    if summary is None:
        return []
    files: list[str] = []

    def _walk(nodes: list[SkillTreeNode]) -> None:
        for n in nodes:
            if n.kind == "file":
                files.append(n.path)
            else:
                _walk(n.children)

    _walk(summary.tree)
    return files


def read_skill_file(
    root: Path,
    skill_name: str,
    rel_path: str,
    *,
    max_bytes: int = MAX_READ_BYTES,
) -> dict[str, Any]:
    """Safely read a file strictly inside `root / skill_name`."""
    skill_dir = _resolve_skill_dir(root, skill_name, must_exist=True)
    cleaned_rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned_rel:
        raise ValueError("File path cannot be empty.")

    host_path, _ = resolve_jailed_path(
        cleaned_rel,
        skill_dir,
        virtual_cwd="/skill",
        virtual_root="/skill",
    )
    if not host_path.exists():
        raise FileNotFoundError(f"File '{cleaned_rel}' not found in skill '{skill_name}'.")
    if not host_path.is_file():
        raise IsADirectoryError(f"Path '{cleaned_rel}' is a directory, not a file.")

    size = host_path.stat().st_size
    rel_norm = host_path.relative_to(skill_dir).as_posix()

    if size > max_bytes:
        raw_head = host_path.read_bytes()[:4096]
        if is_probably_binary(raw_head):
            return {
                "skill_name": skill_dir.name,
                "path": rel_norm,
                "content": "",
                "size": size,
                "is_binary": True,
                "truncated": False,
            }
        with host_path.open("rb") as fh:
            raw = fh.read(max_bytes)
        text = raw.decode("utf-8", errors="replace")
        return {
            "skill_name": skill_dir.name,
            "path": rel_norm,
            "content": text,
            "size": size,
            "is_binary": False,
            "truncated": True,
        }

    raw = host_path.read_bytes()
    if is_probably_binary(raw):
        return {
            "skill_name": skill_dir.name,
            "path": rel_norm,
            "content": "",
            "size": size,
            "is_binary": True,
            "truncated": False,
        }

    return {
        "skill_name": skill_dir.name,
        "path": rel_norm,
        "content": raw.decode("utf-8", errors="replace"),
        "size": size,
        "is_binary": False,
        "truncated": False,
    }


def write_skill_file(
    root: Path,
    skill_name: str,
    rel_path: str,
    content: str,
) -> dict[str, Any]:
    """Create or overwrite a text file strictly inside `root / skill_name`."""
    skill_dir = _resolve_skill_dir(root, skill_name, must_exist=True)
    cleaned_rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned_rel or cleaned_rel.endswith("/"):
        raise ValueError("Invalid relative file path.")

    payload = content.encode("utf-8")
    if len(payload) > MAX_WRITE_BYTES:
        raise ValueError(f"File content exceeds limit of {MAX_WRITE_BYTES // 1024} KB.")

    host_path, _ = resolve_jailed_path(
        cleaned_rel,
        skill_dir,
        virtual_cwd="/skill",
        virtual_root="/skill",
    )
    if host_path == skill_dir or (host_path.exists() and host_path.is_dir()):
        raise IsADirectoryError(f"Path '{cleaned_rel}' is a directory.")

    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(content, encoding="utf-8")
    rel_norm = host_path.relative_to(skill_dir).as_posix()
    return {
        "skill_name": skill_dir.name,
        "path": rel_norm,
        "content": content,
        "size": host_path.stat().st_size,
        "is_binary": False,
        "truncated": False,
    }


def delete_skill_file(root: Path, skill_name: str, rel_path: str) -> None:
    """Delete a file (and clean empty parent subdirectories) inside `root / skill_name`."""
    skill_dir = _resolve_skill_dir(root, skill_name, must_exist=True)
    cleaned_rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned_rel:
        raise ValueError("File path cannot be empty.")

    host_path, _ = resolve_jailed_path(
        cleaned_rel,
        skill_dir,
        virtual_cwd="/skill",
        virtual_root="/skill",
    )
    if host_path == skill_dir:
        raise PermissionError("Cannot delete the skill root directory via file endpoint.")
    if not host_path.exists():
        raise FileNotFoundError(f"File '{cleaned_rel}' not found in skill '{skill_name}'.")
    if host_path.is_dir():
        shutil.rmtree(host_path)
    else:
        host_path.unlink()
        # Prune now-empty intermediate folders up to skill_dir
        parent = host_path.parent
        while parent != skill_dir and parent.is_relative_to(skill_dir):
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
            except OSError:
                break


def create_skill_dir(
    root: Path,
    name: str,
    *,
    description: str = "",
    content_md: str = "",
) -> SkillSummary:
    """Create a new skill package folder containing an initial `SKILL.md`."""
    clean = validate_skill_name(name)
    target = _resolve_skill_dir(root, clean, must_exist=False)
    if target.exists():
        raise FileExistsError(f"Skill '{clean}' already exists.")

    target.mkdir(parents=True, exist_ok=False)
    desc_clean = description.strip() or f"Instructions and resources for {clean}."
    if content_md.strip():
        md_body = content_md
    else:
        md_body = (
            f"---\n"
            f"name: {clean}\n"
            f"description: {desc_clean}\n"
            f"---\n\n"
            f"# {clean}\n\n"
            f"{desc_clean}\n\n"
            f"## Workflow\n\n"
            f"1. Inspect the target environment and gather artifacts.\n"
            f"2. Apply domain checks and verify findings systematically.\n"
        )
    (target / "SKILL.md").write_text(md_body, encoding="utf-8")

    summary = get_skill_summary(root, clean, include_tree=True)
    assert summary is not None
    return summary


def delete_skill_dir(root: Path, skill_name: str) -> None:
    """Delete an entire skill package folder from disk."""
    target = _resolve_skill_dir(root, skill_name, must_exist=True)
    shutil.rmtree(target)


def import_skill_zip(
    root: Path,
    zip_bytes: bytes,
    *,
    filename: str = "skill.zip",
    skill_name: str | None = None,
    overwrite: bool = False,
) -> SkillSummary:
    """Safely extract an uploaded `.zip` skill package into `root / <skill_name>/`."""
    if not zip_bytes:
        raise ValueError("Uploaded ZIP file is empty.")
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise ValueError(f"ZIP file exceeds {MAX_ZIP_BYTES // (1024 * 1024)} MB limit.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid ZIP archive.") from exc

    with zf:
        valid_members: list[tuple[zipfile.ZipInfo, list[str]]] = []
        total_uncompressed = 0

        for info in zf.infolist():
            if info.is_dir():
                continue
            raw_name = info.filename.replace("\\", "/")
            if raw_name.startswith("/") or ":" in raw_name.split("/")[0]:
                raise PermissionError(f"Unsafe absolute path in ZIP: '{info.filename}'")
            norm = posixpath.normpath(raw_name)
            parts = [p for p in norm.split("/") if p and p != "."]
            if not parts or any(p == ".." for p in parts):
                raise PermissionError(f"Unsafe path traversal in ZIP: '{info.filename}'")
            if any(p in IGNORED_NAMES or p == "__MACOSX" for p in parts):
                continue

            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED:
                raise ValueError("ZIP archive uncompressed size exceeds safety limit.")
            valid_members.append((info, parts))

        if not valid_members:
            raise ValueError("ZIP archive does not contain any valid files.")
        if len(valid_members) > MAX_ZIP_FILES:
            raise ValueError(f"ZIP archive contains too many files (max {MAX_ZIP_FILES}).")

        # Detect single top-level wrapper folder (e.g. `my-skill/SKILL.md`, `my-skill/scripts/...`)
        top_segments = {parts[0] for _, parts in valid_members}
        has_single_wrapper = len(top_segments) == 1 and all(len(parts) >= 2 for _, parts in valid_members)
        wrapper_name = next(iter(top_segments)) if has_single_wrapper else None

        inferred = (
            skill_name.strip()
            if (skill_name and skill_name.strip())
            else (wrapper_name or Path(filename).stem or "imported-skill")
        )
        final_name = validate_skill_name(slugify_skill_name(inferred))
        target_dir = _resolve_skill_dir(root, final_name, must_exist=False)

        if target_dir.exists():
            if not overwrite:
                raise FileExistsError(
                    f"Skill '{final_name}' already exists. Enable overwrite or rename the skill."
                )
            shutil.rmtree(target_dir)

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            for info, parts in valid_members:
                rel_parts = parts[1:] if has_single_wrapper else parts
                rel_posix = "/".join(rel_parts)
                dest_path, _ = resolve_jailed_path(
                    rel_posix,
                    target_dir,
                    virtual_cwd="/skill",
                    virtual_root="/skill",
                )
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src_fh, dest_path.open("wb") as dst_fh:
                    shutil.copyfileobj(src_fh, dst_fh)
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

    summary = get_skill_summary(root, final_name, include_tree=True)
    assert summary is not None
    return summary
