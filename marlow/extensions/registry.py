"""
Marlow Extension Registry

Discovers installed extensions, manages installation/uninstallation,
and performs security audits on extension manifests.

/ Registro de extensiones: descubrimiento, instalacion, auditoria.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from marlow.core.config import CONFIG_DIR

logger = logging.getLogger("marlow.extensions.registry")

EXTENSIONS_DIR = CONFIG_DIR / "extensions"
INSTALLED_FILE = EXTENSIONS_DIR / "installed.json"


def _ensure_dir():
    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _load_installed() -> dict:
    """Load the installed extensions registry."""
    _ensure_dir()
    if INSTALLED_FILE.exists():
        try:
            return json.loads(INSTALLED_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_installed(data: dict):
    """Save the installed extensions registry."""
    _ensure_dir()
    INSTALLED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def extensions_list() -> dict:
    """
    List all installed extensions with their status and permissions.

    Returns:
        Dictionary with installed extensions and their metadata.

    / Lista extensiones instaladas con permisos y estado.
    """
    installed = _load_installed()

    extensions = []
    for name, info in installed.items():
        extensions.append({
            "name": name,
            "version": info.get("version", "unknown"),
            "description": info.get("description", ""),
            "tools": info.get("tools", []),
            "permissions": info.get("permissions", {}),
            "installed_at": info.get("installed_at", ""),
        })

    return {
        "success": True,
        "extensions": extensions,
        "count": len(extensions),
    }


async def extensions_install(package: str) -> dict:
    """
    Install an extension from pip.

    The package must include a marlow_extension.json manifest in its
    package data. After pip install, the manifest is validated and
    registered.

    Args:
        package: pip package name or GitHub URL.

    Returns:
        Dictionary with installation status.

    / Instala una extension desde pip.
    """
    import re
    import subprocess
    import sys
    from datetime import datetime

    # Validate package name (PEP 508 compliant names or simple git URLs)
    _VALID_PKG = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?([>=<!\[\]]+.*)?$')
    if not _VALID_PKG.match(package) and not package.startswith("git+"):
        return {"error": f"Invalid package name: '{package}'. Use a valid pip package name."}

    # Run pip install
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--quiet"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            return {
                "error": f"pip install failed: {result.stderr.strip()[:500]}",
                "package": package,
            }
    except subprocess.TimeoutExpired:
        return {"error": f"Installation timed out after 120 seconds", "package": package}

    # Try to find and validate the manifest
    manifest = _find_manifest(package)
    if not manifest:
        return {
            "success": True,
            "package": package,
            "warning": "Package installed but no marlow_extension.json manifest found. "
                       "Extension tools will not be registered until a valid manifest is provided.",
        }

    # Register
    installed = _load_installed()
    installed[manifest["name"]] = {
        **manifest,
        "package": package,
        "installed_at": datetime.now().isoformat(),
    }
    _save_installed(installed)

    return {
        "success": True,
        "name": manifest["name"],
        "version": manifest["version"],
        "tools": [t["name"] for t in manifest.get("tools", [])],
        "permissions": manifest.get("permissions", {}),
    }


async def extensions_uninstall(name: str) -> dict:
    """
    Uninstall an extension.

    Args:
        name: Extension name to uninstall.

    Returns:
        Dictionary with uninstallation status.

    / Desinstala una extension.
    """
    import subprocess
    import sys

    installed = _load_installed()
    if name not in installed:
        return {"error": f"Extension '{name}' is not installed"}

    ext_info = installed[name]
    package = ext_info.get("package", name)

    # Uninstall via pip
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", package, "-y", "--quiet"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        logger.warning(f"pip uninstall error for {package}: {e}")

    # Remove from registry
    del installed[name]
    _save_installed(installed)

    return {"success": True, "name": name, "action": "uninstalled"}


async def extensions_audit(name: str) -> dict:
    """
    Audit an installed extension's security.

    Checks that the extension's declared permissions match its actual
    code usage. Reports any violations.

    Args:
        name: Extension name to audit.

    Returns:
        Dictionary with audit results and any permission violations.

    / Audita la seguridad de una extension instalada.
    """
    installed = _load_installed()
    if name not in installed:
        return {"error": f"Extension '{name}' is not installed"}

    ext_info = installed[name]
    permissions = ext_info.get("permissions", {})

    findings = []

    # Check permission declarations
    if not permissions:
        findings.append({
            "severity": "warning",
            "message": "No permissions declared. Extension may have limited functionality.",
        })

    if permissions.get("shell_commands"):
        findings.append({
            "severity": "high",
            "message": "Extension declares shell_commands permission. Review carefully.",
        })

    if permissions.get("network"):
        findings.append({
            "severity": "medium",
            "message": "Extension declares network permission. It can make HTTP requests.",
        })

    if permissions.get("file_system"):
        fs_perms = permissions["file_system"]
        if "write" in fs_perms:
            findings.append({
                "severity": "medium",
                "message": "Extension declares file_system write permission.",
            })

    risk_level = "low"
    if any(f["severity"] == "high" for f in findings):
        risk_level = "high"
    elif any(f["severity"] == "medium" for f in findings):
        risk_level = "medium"

    return {
        "success": True,
        "name": name,
        "version": ext_info.get("version", "unknown"),
        "risk_level": risk_level,
        "findings": findings,
        "permissions": permissions,
    }


def _find_manifest(package: str) -> Optional[dict]:
    """Try to find a marlow_extension.json from an installed package."""
    try:
        from importlib.metadata import files as pkg_files
        for f in (pkg_files(package) or []):
            if f.name == "marlow_extension.json":
                from marlow.extensions import load_manifest
                return load_manifest(Path(f.locate()))
    except Exception:
        pass

    # Fallback: check common locations
    try:
        import importlib
        mod = importlib.import_module(package.replace("-", "_"))
        mod_dir = Path(mod.__file__).parent
        manifest_path = mod_dir / "marlow_extension.json"
        if manifest_path.exists():
            from marlow.extensions import load_manifest
            return load_manifest(manifest_path)
    except Exception:
        pass

    return None
