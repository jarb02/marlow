"""Marlow Filesystem Tools - search_files and list_directory.

Read-only directory listing and file search via plocate / find fallback.
No system modifications, no file content access.

/ Herramientas de filesystem: busqueda de archivos y listado de directorios.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime

logger = logging.getLogger("marlow.tools.filesystem")

# Directories to exclude when scope="system"
_SYSTEM_EXCLUDES = frozenset({
    "/proc", "/sys", "/dev", "/run", "/tmp", "/snap",
    "/var/cache", "/var/tmp", "/lost+found",
})


def _is_excluded_system_path(path: str) -> bool:
    """Check if a path falls under an excluded system directory."""
    for excl in _SYSTEM_EXCLUDES:
        if path.startswith(excl + "/") or path == excl:
            return True
    # Also exclude .marlow/db/ to avoid internal state leaks
    if "/.marlow/db/" in path:
        return True
    return False


def _apply_post_filters(
    paths: list[str],
    extension: str | None,
    modified_after: str | None,
    scope: str,
) -> list[str]:
    """Apply extension, date, and scope filters to a list of file paths."""
    filtered = []
    cutoff_ts = None

    if modified_after:
        try:
            cutoff_ts = datetime.fromisoformat(modified_after).timestamp()
        except ValueError:
            pass  # ignore bad date, skip filter

    for p in paths:
        # Extension filter
        if extension:
            ext = extension if extension.startswith(".") else f".{extension}"
            if not p.lower().endswith(ext.lower()):
                continue

        # System scope exclusions
        if scope == "system" and _is_excluded_system_path(p):
            continue

        # Date filter
        if cutoff_ts is not None:
            try:
                if os.path.getmtime(p) < cutoff_ts:
                    continue
            except OSError:
                continue  # file inaccessible, skip

        # Verify path still exists (locate DB may be stale)
        if not os.path.exists(p):
            continue

        filtered.append(p)

    return filtered


def _search_locate(
    words: list[str], base_dir: str, max_results: int,
) -> list[str] | None:
    """Try plocate search. Returns list of paths or None if unavailable."""
    try:
        # Build regex: match paths containing ALL words (any order)
        pattern = ".*".join(re.escape(w) for w in words)

        cmd = [
            "plocate", "--ignore-case",
            "--limit", str(max_results * 3),  # over-fetch for post-filtering
            "--regexp", pattern,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        if result.returncode not in (0, 1):  # 1 = no matches
            return None

        lines = [
            line.strip() for line in result.stdout.splitlines()
            if line.strip()
        ]

        # Filter to base directory
        filtered = [
            p for p in lines
            if p.startswith(base_dir + "/") or p == base_dir
        ]
        return filtered

    except FileNotFoundError:
        return None  # plocate not installed
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        logger.debug("plocate failed: %s", e)
        return None


def _search_find(
    words: list[str], base_dir: str, max_results: int,
) -> tuple[list[str], bool]:
    """Fallback search using find + grep pipeline. Returns (paths, timed_out)."""
    if not words:
        return [], False

    # Build find command for first word, escaping for shell safety
    first_word = words[0].replace("'", "'\\''")
    base_quoted = base_dir.replace("'", "'\\''")
    cmd = f"find '{base_quoted}' -iname '*{first_word}*' -readable 2>/dev/null"

    # Pipe through grep for additional words
    for word in words[1:]:
        escaped = word.replace("'", "'\\''")
        cmd += f" | grep -i '{escaped}'"

    # Limit output
    cmd += f" | head -n {max_results * 3}"

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=5,
        )
        lines = [
            line.strip() for line in result.stdout.splitlines()
            if line.strip()
        ]
        return lines, False

    except subprocess.TimeoutExpired:
        return [], True
    except Exception as e:
        logger.debug("find fallback failed: %s", e)
        return [], False


def search_files(
    query: str,
    path: str | None = None,
    scope: str = "home",
    extension: str | None = None,
    modified_after: str | None = None,
    max_results: int = 20,
) -> dict:
    """Search for files by name with fuzzy matching.

    Uses plocate (fast, indexed) with find fallback. Supports partial
    names, multi-keyword AND matching, extension and date filters.

    Args:
        query: Search terms. Multiple words match files containing ALL words.
        path: Specific directory to search. Overrides scope.
        scope: 'home' (default) or 'system' (entire computer).
        extension: Filter by extension, e.g. '.pdf'.
        modified_after: ISO date string, e.g. '2026-03-13'.
        max_results: Max results to return (default 20, max 100).

    Returns:
        Dict with results list, counts, and search metadata.

    / Busca archivos por nombre con matching difuso.
    """
    # Validate query
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    # Clamp max_results
    max_results = max(1, min(max_results, 100))

    # Determine base directory
    if path:
        base_dir = os.path.expanduser(path)
        if not os.path.isdir(base_dir):
            return {"error": f"Directory not found: {path}"}
    elif scope == "system":
        base_dir = "/"
    else:
        base_dir = os.path.expanduser("~")

    # Tokenize query (case insensitive matching handled by tools)
    words = query.strip().split()

    # Try plocate first, fall back to find if no results
    search_method = "locate"
    results = _search_locate(words, base_dir, max_results)
    timed_out = False

    if not results:
        # Fallback to find (plocate unavailable, failed, or returned nothing)
        search_method = "find"
        results, timed_out = _search_find(words, base_dir, max_results)

    # Post-filters
    results = _apply_post_filters(results, extension, modified_after, scope)

    total_found = len(results)
    truncated = total_found > max_results
    results = results[:max_results]

    response = {
        "results": results,
        "total_found": total_found,
        "truncated": truncated,
        "search_method": search_method,
        "query": query,
        "base_dir": base_dir,
    }

    if timed_out:
        response["search_timed_out"] = True

    return response


def list_directory(
    path: str = "~",
    max_results: int = 50,
    show_hidden: bool = False,
) -> dict:
    """List contents of a directory (one level, non-recursive).

    Returns file/directory names, types, sizes, and modification dates.

    Args:
        path: Directory to list (default: home ~).
        max_results: Max entries to return (default 50, max 200).
        show_hidden: Include hidden files starting with '.' (default False).

    Returns:
        Dict with entries list and directory metadata.

    / Lista el contenido de un directorio (un nivel, no recursivo).
    """
    # Expand and validate
    dir_path = os.path.expanduser(path)

    if not os.path.exists(dir_path):
        return {"error": f"Path not found: {path}"}
    if not os.path.isdir(dir_path):
        return {"error": f"Not a directory: {path}"}

    max_results = max(1, min(max_results, 200))

    entries = []
    try:
        with os.scandir(dir_path) as scanner:
            for entry in scanner:
                # Hidden filter
                if not show_hidden and entry.name.startswith("."):
                    continue

                try:
                    stat = entry.stat(follow_symlinks=False)
                    if entry.is_symlink():
                        entry_type = "symlink"
                    elif entry.is_dir(follow_symlinks=False):
                        entry_type = "directory"
                    else:
                        entry_type = "file"

                    modified = datetime.fromtimestamp(
                        stat.st_mtime,
                    ).strftime("%Y-%m-%d %H:%M:%S")

                    entries.append({
                        "name": entry.name,
                        "type": entry_type,
                        "size": stat.st_size if entry_type == "file" else None,
                        "modified": modified,
                    })
                except OSError:
                    # Permission denied or broken symlink
                    entries.append({
                        "name": entry.name,
                        "type": "unknown",
                        "size": None,
                        "modified": None,
                    })

    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except OSError as e:
        return {"error": f"Cannot read directory: {e}"}

    # Sort by name (case insensitive)
    entries.sort(key=lambda e: e["name"].lower())

    total_entries = len(entries)
    truncated = total_entries > max_results
    entries = entries[:max_results]

    return {
        "path": dir_path,
        "entries": entries,
        "total_entries": total_entries,
        "truncated": truncated,
    }


# ─────────────────────────────────────────────────────────────
# Allowed read paths outside HOME
# ─────────────────────────────────────────────────────────────

_READ_ALLOWED_OUTSIDE_HOME = ("/tmp", "/var/log")

# Blocked path fragments for read and write
_BLOCKED_READ_PATTERNS = (
    "/.ssh/", "/.gnupg/", "/.marlow/db/",
    "/secrets.toml", "/secret.toml",
)
_BLOCKED_WRITE_PATTERNS = (
    "/.ssh/", "/.gnupg/", "/.marlow/db/",
    "/.config/marlow/", "/secrets.toml", "/secret.toml",
)

_MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB


def _resolve_and_validate_read(path: str) -> tuple[str, dict | None]:
    """Resolve path, validate for read access. Returns (resolved, error_or_None)."""
    expanded = os.path.expanduser(path)
    resolved = os.path.realpath(expanded)  # resolve symlinks
    home = os.path.expanduser("~")

    # Must be inside HOME or allowed dirs
    inside_home = resolved.startswith(home + "/") or resolved == home
    inside_allowed = any(
        resolved.startswith(d + "/") or resolved == d
        for d in _READ_ALLOWED_OUTSIDE_HOME
    )
    if not inside_home and not inside_allowed:
        return resolved, {"error": f"Access denied: {path} is outside allowed directories"}

    # Blocked patterns
    for pattern in _BLOCKED_READ_PATTERNS:
        if pattern in resolved:
            return resolved, {"error": f"Access denied: {path} contains sensitive data"}

    # Block files with "secret" in basename
    basename = os.path.basename(resolved).lower()
    if "secret" in basename and basename != "secrets":
        return resolved, {"error": f"Access denied: {path} appears to contain secrets"}

    return resolved, None


def _resolve_and_validate_write(path: str) -> tuple[str, dict | None]:
    """Resolve path, validate for write access. Returns (resolved, error_or_None)."""
    expanded = os.path.expanduser(path)
    # For write, resolve the parent (file may not exist yet)
    parent = os.path.dirname(expanded) or "."
    resolved_parent = os.path.realpath(parent)
    resolved = os.path.join(resolved_parent, os.path.basename(expanded))
    home = os.path.expanduser("~")

    # Must be inside HOME
    if not (resolved.startswith(home + "/") or resolved == home):
        return resolved, {"error": f"Access denied: can only write within home directory"}

    # Blocked patterns
    for pattern in _BLOCKED_WRITE_PATTERNS:
        if pattern in resolved:
            return resolved, {"error": f"Access denied: {path} is a protected path"}

    # If target is a symlink, resolve and recheck
    if os.path.islink(expanded):
        link_target = os.path.realpath(expanded)
        if not link_target.startswith(home + "/"):
            return resolved, {"error": f"Access denied: symlink points outside home directory"}

    return resolved, None


def read_file(
    path: str,
    max_size_kb: int = 1024,
    encoding: str = "utf-8",
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict:
    """Read the contents of a text file.

    Supports partial reading by line range, encoding selection, and
    binary file detection. Blocks sensitive paths.

    Args:
        path: Path to read. Supports ~ for home.
        max_size_kb: Max file size in KB (default 1024 = 1MB).
        encoding: Text encoding (default utf-8).
        line_start: First line to read (1-indexed, inclusive).
        line_end: Last line to read (1-indexed, inclusive).

    Returns:
        Dict with content, line count, size, or error.

    / Lee el contenido de un archivo de texto.
    """
    if not path or not path.strip():
        return {"error": "Path cannot be empty"}

    resolved, err = _resolve_and_validate_read(path)
    if err:
        return err

    # Check exists and is file
    if not os.path.exists(resolved):
        return {"error": f"File not found: {path}"}
    if os.path.isdir(resolved):
        return {"error": f"Path is a directory, not a file: {path}"}

    # Check size
    try:
        size_bytes = os.path.getsize(resolved)
    except OSError as e:
        return {"error": f"Cannot access file: {e}"}

    size_kb = round(size_bytes / 1024, 1)
    if size_bytes > max_size_kb * 1024:
        return {
            "error": f"File is {size_kb}KB, exceeds limit of {max_size_kb}KB. "
                     f"Use max_size_kb parameter to increase.",
        }

    # Binary detection
    try:
        with open(resolved, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return {"error": "File appears to be binary. read_file only supports text files."}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except OSError as e:
        return {"error": f"Cannot read file: {e}"}

    # Read text
    try:
        with open(resolved, "r", encoding=encoding) as f:
            content = f.read()
    except UnicodeDecodeError:
        return {"error": f"Cannot decode file with {encoding}. Try encoding='latin-1'."}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except OSError as e:
        return {"error": f"Cannot read file: {e}"}

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    # Line range slicing (1-indexed)
    line_range = None
    if line_start is not None or line_end is not None:
        start = max(1, line_start or 1) - 1  # convert to 0-indexed
        end = min(total_lines, line_end or total_lines)
        content = "".join(lines[start:end])
        line_range = [start + 1, end]

    return {
        "path": resolved,
        "content": content,
        "lines": total_lines,
        "size_kb": size_kb,
        "encoding": encoding,
        **({"line_range": line_range} if line_range else {}),
    }


def write_file(
    path: str,
    content: str,
    overwrite: bool = False,
    create_dirs: bool = False,
    append: bool = False,
) -> dict:
    """Create a new text file or append/overwrite an existing one.

    By default refuses to overwrite existing files. Only writes
    within the home directory. Blocks sensitive paths.

    Args:
        path: Destination path. Supports ~ for home.
        content: Text content to write.
        overwrite: Allow replacing existing files (default False).
        create_dirs: Create parent dirs if needed (default False).
        append: Append to end of file instead of replacing (default False).

    Returns:
        Dict with path, size, line count, and action taken, or error.

    / Crea o escribe un archivo de texto.
    """
    if not path or not path.strip():
        return {"error": "Path cannot be empty"}
    if content is None:
        return {"error": "Content cannot be None"}

    resolved, err = _resolve_and_validate_write(path)
    if err:
        return err

    # Content size limit
    content_bytes = len(content.encode("utf-8"))
    if content_bytes > _MAX_WRITE_BYTES:
        return {
            "error": f"Content is {round(content_bytes / 1024 / 1024, 1)}MB, "
                     f"exceeds limit of 5MB.",
        }

    parent = os.path.dirname(resolved)
    dirs_created = False

    # Parent directory check
    if not os.path.isdir(parent):
        if not create_dirs:
            return {
                "error": f"Directory does not exist: {os.path.dirname(path)}. "
                         f"Use create_dirs=True to create it.",
            }
        try:
            os.makedirs(parent, exist_ok=True)
            dirs_created = True
        except PermissionError:
            return {"error": f"Permission denied creating directories: {parent}"}
        except OSError as e:
            return {"error": f"Cannot create directories: {e}"}

    # Existing file handling
    file_exists = os.path.exists(resolved)
    if file_exists and not overwrite and not append:
        return {
            "error": f"File already exists: {path}. "
                     f"Use overwrite=True to replace or append=True to add to end.",
        }

    # Determine action and write mode
    if append and file_exists:
        action = "appended"
        mode = "a"
    elif file_exists:
        action = "overwritten"
        mode = "w"
    else:
        action = "created"
        mode = "w"

    try:
        with open(resolved, mode, encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except OSError as e:
        return {"error": f"Cannot write file: {e}"}

    # Get final size
    final_size = os.path.getsize(resolved)
    written_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

    return {
        "path": resolved,
        "size_kb": round(final_size / 1024, 1),
        "lines": written_lines,
        "action": action,
        "dirs_created": dirs_created,
    }
