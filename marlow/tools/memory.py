"""
Marlow Memory Tool

Persistent key-value storage across sessions. Data stored as JSON
in ~/.marlow/memory/, organized by category.

/ Almacenamiento persistente entre sesiones en ~/.marlow/memory/.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from marlow.core.config import CONFIG_DIR

logger = logging.getLogger("marlow.tools.memory")

MEMORY_DIR = CONFIG_DIR / "memory"

VALID_CATEGORIES = ("general", "preferences", "projects", "tasks")


def _ensure_dir():
    """Create memory directory if it doesn't exist."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_category(category: str) -> dict:
    """Load a category file, returning empty dict if missing."""
    cat_file = MEMORY_DIR / f"{category}.json"
    if cat_file.exists():
        try:
            return json.loads(cat_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load memory file {cat_file}: {e}")
    return {}


def _save_category(category: str, data: dict):
    """Save data to a category file."""
    _ensure_dir()
    cat_file = MEMORY_DIR / f"{category}.json"
    cat_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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

    data = _load_category(category)

    is_update = key in data
    data[key] = {
        "value": value,
        "created": data.get(key, {}).get("created", datetime.now().isoformat()),
        "updated": datetime.now().isoformat(),
    }

    _save_category(category, data)

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
    _ensure_dir()

    if key and category:
        data = _load_category(category)
        if key not in data:
            return {"error": f"Key '{key}' not found in category '{category}'"}
        return {"success": True, "key": key, "category": category, **data[key]}

    elif category:
        if category not in VALID_CATEGORIES:
            return {"error": f"Invalid category '{category}'. Valid: {VALID_CATEGORIES}"}
        data = _load_category(category)
        return {"success": True, "category": category, "keys": list(data.keys()), "count": len(data)}

    elif key:
        for cat in VALID_CATEGORIES:
            data = _load_category(cat)
            if key in data:
                return {"success": True, "key": key, "category": cat, **data[key]}
        return {"error": f"Key '{key}' not found in any category"}

    else:
        categories = {}
        for cat in VALID_CATEGORIES:
            data = _load_category(cat)
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

    data = _load_category(category)
    if key not in data:
        return {"error": f"Key '{key}' not found in category '{category}'"}

    del data[key]
    _save_category(category, data)

    return {"success": True, "key": key, "category": category, "action": "deleted"}


async def memory_list() -> dict:
    """
    List all stored memories organized by category.

    Returns:
        Dictionary with all categories and their keys.

    / Lista todas las memorias organizadas por categoria.
    """
    return await memory_recall()
