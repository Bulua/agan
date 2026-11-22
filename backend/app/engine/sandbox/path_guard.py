from __future__ import annotations

import fnmatch
import os
import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any

IGNORED_WALK_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "dist",
    }
)


def normalize_extensions(raw_exts: list[str] | str | None) -> list[str]:
    """Normalize allowed extension list into sorted lowercase `.ext` strings."""
    if not raw_exts:
        return []
    items = raw_exts.split(",") if isinstance(raw_exts, str) else list(raw_exts)
    normalized: list[str] = []
    for item in items:
        s = str(item).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = f".{s}"
        if s not in normalized:
            normalized.append(s)
    return normalized


def resolve_jailed_path(
    raw_path: str,
    root_dir: Path,
    *,
    virtual_cwd: str = "/workspace",
    virtual_root: str = "/workspace",
) -> tuple[Path, str]:
    """Resolve a user/LLM file path strictly inside `root_dir`.

    Supports:
    - Relative paths (resolved against `virtual_cwd` inside `virtual_root`)
    - Virtual sandbox paths prefixed with `/workspace` (mapped onto `root_dir`)
    - Host absolute paths ONLY if they resolve strictly inside `root_dir`

    Raises `PermissionError` on any attempt to escape `root_dir` (via `..`,
    external absolute paths, Windows drive letters, or symlinks).
    """
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        raise ValueError("File path cannot be empty.")
    if "\x00" in cleaned:
        raise PermissionError("Access denied: path contains invalid NUL byte.")

    root = Path(root_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # Normalize slashes for cross-platform safety
    slash_path = cleaned.replace("\\", "/")

    # 1. Check if caller provided a real host absolute path (e.g., D:/workspaces/foo.txt)
    raw_host = Path(cleaned)
    if raw_host.is_absolute():
        resolved_host = raw_host.resolve()
        if resolved_host.is_relative_to(root):
            rel_posix = resolved_host.relative_to(root).as_posix()
            virt = (
                virtual_root
                if rel_posix in ("", ".")
                else posixpath.join(virtual_root, rel_posix)
            )
            return resolved_host, virt
        # If it wasn't inside root, check if it's a virtual `/workspace/...` POSIX path
        if not (slash_path == virtual_root or slash_path.startswith(virtual_root + "/")):
            raise PermissionError(
                f"Access denied: path '{raw_path}' is outside allowed root directory '{root}'."
            )

    # 2. Handle POSIX-style absolute paths (including `/workspace/...` on Windows where Path('/x').is_absolute() is False)
    vroot = virtual_root.rstrip("/")
    if slash_path.startswith("/"):
        norm_virt = posixpath.normpath(slash_path)
        if norm_virt == vroot:
            rel_part = ""
        elif norm_virt.startswith(vroot + "/"):
            rel_part = norm_virt[len(vroot) + 1 :]
        else:
            # Check if on POSIX this absolute path actually lies inside root_dir
            candidate_abs = Path(slash_path).resolve()
            if candidate_abs.is_relative_to(root):
                rel_posix = candidate_abs.relative_to(root).as_posix()
                virt = (
                    vroot if rel_posix in ("", ".") else posixpath.join(vroot, rel_posix)
                )
                return candidate_abs, virt
            raise PermissionError(
                f"Access denied: path '{raw_path}' escapes allowed workspace root '{vroot}'."
            )
    else:
        # Relative path: resolve against virtual_cwd inside virtual_root
        norm_cwd = posixpath.normpath(virtual_cwd.replace("\\", "/"))
        if not (norm_cwd == vroot or norm_cwd.startswith(vroot + "/")):
            norm_cwd = vroot
        joined_virt = posixpath.normpath(posixpath.join(norm_cwd, slash_path))
        if joined_virt == vroot:
            rel_part = ""
        elif joined_virt.startswith(vroot + "/"):
            rel_part = joined_virt[len(vroot) + 1 :]
        else:
            raise PermissionError(
                f"Access denied: relative path '{raw_path}' escapes allowed workspace root '{vroot}'."
            )

    # 3. Map to host filesystem and verify symlink-resolved containment
    candidate = (root / rel_part).resolve() if rel_part else root
    if not candidate.is_relative_to(root):
        raise PermissionError(
            f"Access denied: resolved path '{candidate}' escapes allowed root directory '{root}'."
        )

    rel_posix = candidate.relative_to(root).as_posix()
    final_virt = vroot if rel_posix in ("", ".") else posixpath.join(vroot, rel_posix)
    return candidate, final_virt


def validate_extension(host_path: Path, allowed_extensions: list[str] | None) -> None:
    allowed = normalize_extensions(allowed_extensions)
    if not allowed:
        return
    ext = host_path.suffix.lower()
    if ext not in allowed:
        raise PermissionError(
            f"Access denied: extension '{ext or '(none)'}' is not allowed. "
            f"Allowed extensions: {', '.join(allowed)}"
        )


def validate_file_size(size_bytes: int, max_file_size_kb: int) -> None:
    limit_kb = max(1, int(max_file_size_kb))
    limit_bytes = limit_kb * 1024
    if size_bytes > limit_bytes:
        raise ValueError(
            f"File size ({size_bytes} bytes) exceeds configured limit of {limit_kb} KB "
            f"({limit_bytes} bytes)."
        )


def is_probably_binary(raw_bytes: bytes) -> bool:
    sample = raw_bytes[:4096]
    return b"\x00" in sample


def read_jailed_text_file(
    host_path: Path,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    show_line_numbers: bool = False,
    max_file_size_kb: int = 512,
    allowed_extensions: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Safely read a UTF-8 text file with size, extension, binary, and line-range guards."""
    validate_extension(host_path, allowed_extensions)

    if not host_path.exists():
        raise FileNotFoundError(f"File not found: {host_path.name}")
    if not host_path.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: {host_path.name}")

    file_size = host_path.stat().st_size
    limit_bytes = max(1, int(max_file_size_kb)) * 1024

    # Allow reading line slices from slightly larger files (up to 4x limit) while bounding output
    hard_cap = limit_bytes * 4 if (start_line is not None or end_line is not None) else limit_bytes
    if file_size > hard_cap:
        hint = (
            " Specify `start_line` and `end_line` to read a smaller range."
            if start_line is None and end_line is None
            else ""
        )
        raise ValueError(
            f"File '{host_path.name}' is {file_size} bytes, exceeding the {max_file_size_kb} KB limit.{hint}"
        )

    raw_bytes = host_path.read_bytes()
    if is_probably_binary(raw_bytes):
        raise ValueError(
            f"File '{host_path.name}' ({file_size} bytes) appears to be binary; "
            "use shell tools (`file`, `strings`, `xxd`) to inspect binary files."
        )

    text = raw_bytes.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    total_lines = len(all_lines)

    if start_line is not None or end_line is not None:
        s = max(1, int(start_line or 1))
        e = min(total_lines, int(end_line)) if end_line is not None else total_lines
        if s > total_lines and total_lines > 0:
            selected_lines: list[str] = []
        else:
            selected_lines = all_lines[s - 1 : e]
        first_line_no = s
    else:
        selected_lines = all_lines
        first_line_no = 1
        s = 1
        e = total_lines

    if show_line_numbers:
        rendered = "\n".join(
            f"{idx}: {line}" for idx, line in enumerate(selected_lines, start=first_line_no)
        )
    else:
        rendered = "\n".join(selected_lines)

    truncated = False
    if len(rendered.encode("utf-8")) > limit_bytes:
        rendered = rendered[:limit_bytes] + "\n... [output truncated by max_file_size_kb limit]"
        truncated = True

    meta = {
        "host_path": str(host_path),
        "bytes": file_size,
        "total_lines": total_lines,
        "start_line": s,
        "end_line": e,
        "truncated": truncated,
    }
    return rendered, meta


def write_jailed_text_file(
    host_path: Path,
    content: str,
    *,
    mode: str = "overwrite",
    allow_overwrite: bool = True,
    max_file_size_kb: int = 512,
    allowed_extensions: list[str] | None = None,
) -> dict[str, Any]:
    """Safely write or append UTF-8 text to a jailed host file with size & overwrite guards."""
    validate_extension(host_path, allowed_extensions)

    if host_path.exists() and host_path.is_dir():
        raise IsADirectoryError(f"Cannot write to directory path: {host_path.name}")

    write_mode = (mode or "overwrite").strip().lower()
    if write_mode not in ("overwrite", "append"):
        raise ValueError("Parameter 'mode' must be either 'overwrite' or 'append'.")

    payload_bytes = content.encode("utf-8")
    existing_size = host_path.stat().st_size if host_path.exists() else 0

    if write_mode == "overwrite" and host_path.exists() and not allow_overwrite:
        raise FileExistsError(
            f"Overwriting existing file '{host_path.name}' is disabled by tool policy "
            "(allow_overwrite=false). Use mode='append' or write to a new path."
        )

    resulting_size = (existing_size + len(payload_bytes)) if write_mode == "append" else len(payload_bytes)
    validate_file_size(resulting_size, max_file_size_kb)

    host_path.parent.mkdir(parents=True, exist_ok=True)
    if write_mode == "append":
        with host_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    else:
        host_path.write_text(content, encoding="utf-8", newline="\n")

    return {
        "host_path": str(host_path),
        "mode": write_mode,
        "bytes_written": len(payload_bytes),
        "total_bytes": host_path.stat().st_size,
    }


def edit_jailed_text_file(
    host_path: Path,
    old_text: str,
    new_text: str,
    *,
    replace_all: bool = False,
    max_file_size_kb: int = 512,
    allowed_extensions: list[str] | None = None,
) -> dict[str, Any]:
    """Safely apply a targeted exact-string replacement on a jailed UTF-8 text file."""
    validate_extension(host_path, allowed_extensions)

    if not old_text:
        raise ValueError("Parameter 'old_text' must be non-empty.")
    if not host_path.exists():
        raise FileNotFoundError(f"File not found: {host_path.name}")
    if not host_path.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: {host_path.name}")

    existing_size = host_path.stat().st_size
    validate_file_size(existing_size, max_file_size_kb)

    raw_bytes = host_path.read_bytes()
    if is_probably_binary(raw_bytes):
        raise ValueError(f"File '{host_path.name}' appears to be binary and cannot be edited as text.")

    original = raw_bytes.decode("utf-8", errors="replace")
    uses_crlf = "\r\n" in original
    match_target = old_text
    match_replacement = new_text
    if uses_crlf and "\r\n" not in old_text and "\n" in old_text:
        original_lf = original.replace("\r\n", "\n")
        if original_lf.count(old_text) > 0:
            match_target = old_text.replace("\n", "\r\n")
            match_replacement = new_text.replace("\r\n", "\n").replace("\n", "\r\n")

    occurrences = original.count(match_target)
    if occurrences == 0:
        raise ValueError(
            f"Target `old_text` was not found in '{host_path.name}'. "
            "Read the file first to verify exact whitespace and indentation."
        )
    if occurrences > 1 and not replace_all:
        raise ValueError(
            f"Target `old_text` matched {occurrences} times in '{host_path.name}'. "
            "Include more surrounding lines to make the match unique, or pass `replace_all=true`."
        )

    updated = (
        original.replace(match_target, match_replacement)
        if replace_all
        else original.replace(match_target, match_replacement, 1)
    )
    replacements = occurrences if replace_all else 1
    updated_bytes = updated.encode("utf-8")
    validate_file_size(len(updated_bytes), max_file_size_kb)

    with host_path.open("wb") as fh:
        fh.write(updated_bytes)

    old_line_cnt = len(old_text.splitlines()) or 1
    new_line_cnt = len(new_text.splitlines()) if new_text else 0
    return {
        "host_path": str(host_path),
        "replacements": replacements,
        "lines_removed": old_line_cnt * replacements,
        "lines_added": new_line_cnt * replacements,
        "total_bytes": len(updated_bytes),
        "total_lines": len(updated.splitlines()),
    }


def _match_glob(rel_posix: str, filename: str, pattern: str) -> bool:
    pat = (pattern or "*").strip().replace("\\", "/")
    if not pat or pat in ("*", "**", "**/*"):
        return True
    if "/" not in pat:
        return fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(filename.lower(), pat.lower())
    # Handle leading `**/` cleanly so `**/*.py` also matches top-level `foo.py`
    if pat.startswith("**/"):
        tail = pat[3:]
        if fnmatch.fnmatch(rel_posix, tail) or fnmatch.fnmatch(filename, tail):
            return True
    return PurePosixPath(rel_posix).match(pat) or fnmatch.fnmatch(rel_posix, pat)


def list_jailed_files(
    base_dir: Path,
    root_dir: Path,
    *,
    pattern: str = "*",
    max_depth: int = 4,
    max_results: int = 100,
    include_dirs: bool = True,
    virtual_root: str = "/workspace",
) -> dict[str, Any]:
    """Safely list files/directories strictly within `root_dir` starting at `base_dir`."""
    root = Path(root_dir).resolve()
    base = Path(base_dir).resolve()
    if not base.is_relative_to(root):
        raise PermissionError("Access denied: directory escapes allowed workspace root.")
    if not base.exists():
        raise FileNotFoundError(f"Directory not found: {base.name}")
    if not base.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {base.name}")

    depth_cap = max(1, min(int(max_depth), 10))
    res_cap = max(1, min(int(max_results), 500))
    vroot = virtual_root.rstrip("/")

    entries: list[dict[str, Any]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(base):
        cur = Path(dirpath).resolve()
        if not cur.is_relative_to(root):
            dirnames[:] = []
            continue

        rel_from_base = cur.relative_to(base)
        depth = 0 if str(rel_from_base) == "." else len(rel_from_base.parts)

        # Prune ignored directories and enforce max_depth
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORED_WALK_DIRS and not d.startswith(".")
        )
        if depth >= depth_cap:
            dirnames[:] = []

        if include_dirs and depth < depth_cap:
            for d in dirnames:
                d_path = (cur / d).resolve()
                if not d_path.is_relative_to(root):
                    continue
                rel_base_posix = d_path.relative_to(base).as_posix()
                if not _match_glob(rel_base_posix, d, pattern):
                    continue
                rel_root_posix = d_path.relative_to(root).as_posix()
                entries.append(
                    {
                        "path": posixpath.join(vroot, rel_root_posix),
                        "rel_path": rel_base_posix,
                        "kind": "dir",
                        "size": 0,
                    }
                )
                if len(entries) >= res_cap:
                    truncated = True
                    break
            if truncated:
                break

        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            f_path = (cur / fn).resolve()
            if not f_path.is_relative_to(root):
                continue
            rel_base_posix = f_path.relative_to(base).as_posix()
            if not _match_glob(rel_base_posix, fn, pattern):
                continue
            try:
                f_size = f_path.stat().st_size
            except OSError:
                f_size = 0
            rel_root_posix = f_path.relative_to(root).as_posix()
            entries.append(
                {
                    "path": posixpath.join(vroot, rel_root_posix),
                    "rel_path": rel_base_posix,
                    "kind": "file",
                    "size": f_size,
                }
            )
            if len(entries) >= res_cap:
                truncated = True
                break
        if truncated:
            break

    return {
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
    }


def grep_jailed_files(
    target_path: Path,
    root_dir: Path,
    *,
    pattern: str,
    file_glob: str | None = None,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 50,
    max_file_size_kb: int = 512,
    virtual_root: str = "/workspace",
) -> dict[str, Any]:
    """Safely search UTF-8 text files inside `root_dir` for literal or regex matches."""
    if not pattern:
        raise ValueError("Parameter 'pattern' must be non-empty.")

    root = Path(root_dir).resolve()
    target = Path(target_path).resolve()
    if not target.is_relative_to(root):
        raise PermissionError("Access denied: target escapes allowed workspace root.")
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {target.name}")

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern if is_regex else re.escape(pattern), flags)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression '{pattern}': {exc}") from exc

    res_cap = max(1, min(int(max_results), 200))
    size_cap = max(1, int(max_file_size_kb)) * 1024
    vroot = virtual_root.rstrip("/")

    files_to_scan: list[Path] = []
    if target.is_file():
        files_to_scan.append(target)
    else:
        for dirpath, dirnames, filenames in os.walk(target):
            cur = Path(dirpath).resolve()
            if not cur.is_relative_to(root):
                dirnames[:] = []
                continue
            dirnames[:] = sorted(
                d for d in dirnames if d not in IGNORED_WALK_DIRS and not d.startswith(".")
            )
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                fp = (cur / fn).resolve()
                if not fp.is_relative_to(root):
                    continue
                if file_glob:
                    rel_t = fp.relative_to(target).as_posix()
                    if not _match_glob(rel_t, fn, file_glob):
                        continue
                files_to_scan.append(fp)

    matches: list[dict[str, Any]] = []
    files_searched = 0
    files_matched_set: set[str] = set()
    truncated = False

    for fp in files_to_scan:
        try:
            st = fp.stat()
            if st.st_size > size_cap or st.st_size == 0:
                continue
            raw = fp.read_bytes()
        except OSError:
            continue
        if is_probably_binary(raw):
            continue

        files_searched += 1
        rel_root_posix = fp.relative_to(root).as_posix()
        virt_path = posixpath.join(vroot, rel_root_posix)
        text = raw.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                snippet = line if len(line) <= 300 else (line[:300] + "...")
                matches.append(
                    {
                        "path": virt_path,
                        "rel_path": rel_root_posix,
                        "line": line_no,
                        "text": snippet,
                    }
                )
                files_matched_set.add(virt_path)
                if len(matches) >= res_cap:
                    truncated = True
                    break
        if truncated:
            break

    return {
        "matches": matches,
        "match_count": len(matches),
        "files_searched": files_searched,
        "files_matched": len(files_matched_set),
        "truncated": truncated,
    }
