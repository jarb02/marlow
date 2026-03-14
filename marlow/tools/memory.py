"""
Marlow Memory Tool

Persistent key-value storage across sessions. Uses SQLite (state.db)
when initialized by the daemon, falls back to JSON files in
~/.marlow/memory/ for standalone use and tests.

/ Almacenamiento persistente entre sesiones — SQLite o JSON.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from marlow.core.config import CONFIG_DIR

logger = logging.getLogger("marlow.tools.memory")

MEMORY_DIR = CONFIG_DIR / "memory"

VALID_CATEGORIES = ("general", "preferences", "projects", "tasks")

# SQLite backend (set by daemon via init_sqlite)
_db_path: Optional[Path] = None


def init_sqlite(db_path) -> None:
    """Switch memory tools to SQLite backend.

    Called by the daemon during startup after DatabaseManager creates schema.
    The memory table in state.db is used with tier='long' and id='kv:<cat>:<key>'.
    """
    global _db_path
    _db_path = Path(db_path)
    logger.info("Memory tools switched to SQLite: %s", db_path)


def _get_conn() -> Optional[sqlite3.Connection]:
    """Get a sync SQLite connection if backend is initialized."""
    if _db_path and _db_path.exists():
        conn = sqlite3.connect(str(_db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    return None


def _ensure_dir():
    """Create memory directory if it doesn't exist."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_json_category(category: str) -> dict:
    """Load a category from JSON file."""
    cat_file = MEMORY_DIR / f"{category}.json"
    if cat_file.exists():
        try:
            return json.loads(cat_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file %s: %s", cat_file, e)
    return {}


def _save_json_category(category: str, data: dict):
    """Save data to a JSON category file."""
    _ensure_dir()
    cat_file = MEMORY_DIR / f"{category}.json"
    cat_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_category(category: str) -> dict:
    """Load a category — SQLite first, then JSON fallback.

    Returns dict of {key: {value, created, updated}}.
    Used by context_builder for sync reads.
    """
    conn = _get_conn()
    if conn:
        try:
            cursor = conn.execute(
                "SELECT id, content FROM memory WHERE tier = 'long' AND category = ?",
                (category,),
            )
            data = {}
            for row in cursor:
                # id format: "kv:<category>:<key>"
                mem_id = row[0]
                parts = mem_id.split(":", 2)
                key = parts[2] if len(parts) == 3 else mem_id
                data[key] = json.loads(row[1])
            conn.close()
            if data:
                return data
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    return _load_json_category(category)


async def memory_save(
    key: str,
    value: str,
    category: str = "general",
) -> dict:
    """
    Save a value persistently across sessions.

    Args:
        key: Unique identifier for this memory.
        value: The text/data to store.
        category: Organization category: general, preferences, projects, tasks.

    Returns:
        Dictionary with save confirmation.

    / Guarda un valor persistente entre sesiones.
    """
    if category not in VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Valid: {VALID_CATEGORIES}"}

    if not key or not key.strip():
        return {"error": "Key cannot be empty"}

    key = key.strip().lower().replace("  ", " ")

    now = datetime.now().isoformat()

    conn = _get_conn()
    if conn:
        try:
            mem_id = f"kv:{category}:{key}"
            # Check if exists for created timestamp
            cursor = conn.execute(
                "SELECT content FROM memory WHERE id = ?", (mem_id,),
            )
            row = cursor.fetchone()
            if row:
                existing = json.loads(row[0])
                created = existing.get("created", now)
                is_update = True
            else:
                created = now
                is_update = False

            content = json.dumps(
                {"value": value, "created": created, "updated": now},
                ensure_ascii=False,
            )
            conn.execute(
                """INSERT INTO memory
                       (id, tier, category, content, relevance,
                        access_count, created_at, tags)
                   VALUES (?, 'long', ?, ?, 1.0, 0, ?, '[]')
                   ON CONFLICT(id) DO UPDATE SET
                       content = excluded.content""",
                (mem_id, category, content, now),
            )
            conn.commit()
            conn.close()
            return {
                "success": True,
                "key": key,
                "category": category,
                "action": "updated" if is_update else "saved",
            }
        except Exception as e:
            logger.warning("SQLite memory_save error: %s", e)
            try:
                conn.close()
            except Exception:
                pass

    # JSON fallback
    data = _load_json_category(category)
    is_update = key in data
    data[key] = {
        "value": value,
        "created": data.get(key, {}).get("created", now),
        "updated": now,
    }
    _save_json_category(category, data)
    return {
        "success": True,
        "key": key,
        "category": category,
        "action": "updated" if is_update else "saved",
    }


async def memory_recall(
    key: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    """
    Recall stored memories.

    - key + category: get specific memory
    - category only: list all keys in that category
    - key only: search all categories for that key
    - neither: list all categories and their keys

    Args:
        key: Key to look up.
        category: Category to search in.

    Returns:
        Dictionary with the recalled memory or a listing.

    / Recupera memorias almacenadas.
    """
    if key:
        key = key.strip().lower().replace("  ", " ")

    conn = _get_conn()

    if key and category:
        if conn:
            try:
                mem_id = f"kv:{category}:{key}"
                cursor = conn.execute(
                    "SELECT content FROM memory WHERE id = ?", (mem_id,),
                )
                row = cursor.fetchone()
                conn.close()
                if not row:
                    return {"error": f"Key '{key}' not found in category '{category}'"}
                entry = json.loads(row[0])
                return {"success": True, "key": key, "category": category, **entry}
            except Exception as e:
                logger.warning("SQLite memory_recall error: %s", e)
                try:
                    conn.close()
                except Exception:
                    pass

        data = _load_json_category(category)
        if key not in data:
            return {"error": f"Key '{key}' not found in category '{category}'"}
        return {"success": True, "key": key, "category": category, **data[key]}

    elif category:
        if category not in VALID_CATEGORIES:
            return {"error": f"Invalid category '{category}'. Valid: {VALID_CATEGORIES}"}

        if conn:
            try:
                cursor = conn.execute(
                    "SELECT id FROM memory WHERE tier = 'long' AND category = ? ORDER BY id",
                    (category,),
                )
                keys = []
                for row in cursor:
                    parts = row[0].split(":", 2)
                    keys.append(parts[2] if len(parts) == 3 else row[0])
                conn.close()
                return {"success": True, "category": category, "keys": keys, "count": len(keys)}
            except Exception as e:
                logger.warning("SQLite memory_recall list error: %s", e)
                try:
                    conn.close()
                except Exception:
                    pass

        data = _load_json_category(category)
        return {"success": True, "category": category, "keys": list(data.keys()), "count": len(data)}

    elif key:
        for cat in VALID_CATEGORIES:
            if conn:
                try:
                    mem_id = f"kv:{cat}:{key}"
                    cursor = conn.execute(
                        "SELECT content FROM memory WHERE id = ?", (mem_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        entry = json.loads(row[0])
                        conn.close()
                        return {"success": True, "key": key, "category": cat, **entry}
                except Exception:
                    pass
            else:
                data = _load_json_category(cat)
                if key in data:
                    return {"success": True, "key": key, "category": cat, **data[key]}
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        return {"error": f"Key '{key}' not found in any category"}

    else:
        categories = {}
        if conn:
            try:
                cursor = conn.execute(
                    "SELECT category, id FROM memory WHERE tier = 'long' ORDER BY category, id",
                )
                for row in cursor:
                    cat = row[0]
                    parts = row[1].split(":", 2)
                    k = parts[2] if len(parts) == 3 else row[1]
                    if cat not in categories:
                        categories[cat] = {"keys": [], "count": 0}
                    categories[cat]["keys"].append(k)
                    categories[cat]["count"] += 1
                conn.close()
                if categories:
                    return {
                        "success": True,
                        "categories": categories,
                        "total_categories": len(categories),
                    }
            except Exception as e:
                logger.warning("SQLite memory_list error: %s", e)
                try:
                    conn.close()
                except Exception:
                    pass

        # JSON fallback
        _ensure_dir()
        for cat in VALID_CATEGORIES:
            data = _load_json_category(cat)
            if data:
                categories[cat] = {"keys": list(data.keys()), "count": len(data)}
        return {
            "success": True,
            "categories": categories,
            "total_categories": len(categories),
        }


async def memory_delete(
    key: str,
    category: str = "general",
) -> dict:
    """
    Delete a specific memory.

    Args:
        key: Key to delete.
        category: Category it belongs to.

    Returns:
        Dictionary with deletion confirmation.

    / Elimina una memoria especifica.
    """
    if category not in VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Valid: {VALID_CATEGORIES}"}

    if key:
        key = key.strip().lower().replace("  ", " ")

    conn = _get_conn()
    if conn:
        try:
            mem_id = f"kv:{category}:{key}"
            cursor = conn.execute(
                "DELETE FROM memory WHERE id = ?", (mem_id,),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            conn.close()
            if deleted:
                return {"success": True, "key": key, "category": category, "action": "deleted"}
            return {"error": f"Key '{key}' not found in category '{category}'"}
        except Exception as e:
            logger.warning("SQLite memory_delete error: %s", e)
            try:
                conn.close()
            except Exception:
                pass

    # JSON fallback
    data = _load_json_category(category)
    if key not in data:
        return {"error": f"Key '{key}' not found in category '{category}'"}
    del data[key]
    _save_json_category(category, data)
    return {"success": True, "key": key, "category": category, "action": "deleted"}


async def memory_list() -> dict:
    """
    List all stored memories organized by category.

    Returns:
        Dictionary with all categories and their keys.

    / Lista todas las memorias organizadas por categoria.
    """
    return await memory_recall()


def import_json_to_sqlite(db_path) -> int:
    """One-time migration: import all JSON category files into SQLite.

    Returns total number of entries imported.
    """
    conn = None
    total = 0
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        now = datetime.now().isoformat()

        for category in VALID_CATEGORIES:
            data = _load_json_category(category)
            for key, entry in data.items():
                mem_id = f"kv:{category}:{key}"
                content = json.dumps(entry, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO memory
                           (id, tier, category, content, relevance,
                            access_count, created_at, tags)
                       VALUES (?, 'long', ?, ?, 1.0, 0, ?, '[]')
                       ON CONFLICT(id) DO NOTHING""",
                    (mem_id, category, content, now),
                )
                total += 1
        conn.commit()
    except Exception as e:
        logger.warning("JSON to SQLite migration error: %s", e)
    finally:
        if conn:
            conn.close()
    return total
