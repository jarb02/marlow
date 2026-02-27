"""
Marlow Extension System

Discovers, loads, and manages community extensions with sandboxed permissions.
Extensions are pip packages with a `marlow_extension.json` manifest declaring
what permissions they need.

/ Sistema de extensiones con permisos sandboxed.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.extensions")


def load_manifest(manifest_path: Path) -> Optional[dict]:
    """Load and validate an extension manifest file."""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load manifest {manifest_path}: {e}")
        return None

    required_fields = ("name", "version", "description", "permissions", "tools")
    for field in required_fields:
        if field not in data:
            logger.warning(f"Manifest {manifest_path} missing required field: {field}")
            return None

    return data
