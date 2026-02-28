#!/usr/bin/env python3
"""
Marlow Installer — Simple setup for non-technical users.

Usage:
    python install.py

Steps:
    1. Check Python >= 3.10
    2. Install Marlow (pip install -e .)
    3. Run first-use setup wizard
    4. Detect and configure MCP clients
    5. Print final instructions

/ Instalador simple para usuarios no tecnicos.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


# ── Bilingual output ──

def p(en: str, es: str) -> None:
    """Print bilingual message (EN first, ES second)."""
    print(f"  {en}")
    print(f"  {es}")
    print()


def header(en: str, es: str) -> None:
    """Print section header."""
    print()
    print("=" * 60)
    print(f"  {en}")
    print(f"  {es}")
    print("=" * 60)


# ── Step 1: Check Python ──

def check_python() -> bool:
    """Verify Python >= 3.10."""
    header(
        "Step 1/4: Checking Python version...",
        "Paso 1/4: Verificando version de Python...",
    )
    ver = sys.version_info
    if ver >= (3, 10):
        p(
            f"  Python {ver.major}.{ver.minor}.{ver.micro} — OK",
            f"  Python {ver.major}.{ver.minor}.{ver.micro} — OK",
        )
        return True
    else:
        p(
            f"  Python {ver.major}.{ver.minor} found — Marlow requires Python 3.10+",
            f"  Python {ver.major}.{ver.minor} encontrado — Marlow requiere Python 3.10+",
        )
        p(
            "  Download Python: https://www.python.org/downloads/",
            "  Descargar Python: https://www.python.org/downloads/",
        )
        return False


# ── Step 2: Install Marlow ──

def install_marlow() -> bool:
    """Install Marlow in editable mode."""
    header(
        "Step 2/4: Installing Marlow...",
        "Paso 2/4: Instalando Marlow...",
    )

    # Check if pyproject.toml exists in current directory
    project_dir = Path(__file__).parent
    if not (project_dir / "pyproject.toml").exists():
        p(
            "  Error: pyproject.toml not found. Run this script from the Marlow project root.",
            "  Error: pyproject.toml no encontrado. Ejecuta este script desde la raiz del proyecto.",
        )
        return False

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(project_dir)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            p(
                "  Marlow installed successfully!",
                "  Marlow instalado exitosamente!",
            )
            return True
        else:
            print(f"  pip error: {result.stderr[:500]}")
            p(
                "  Installation failed. Check the error above.",
                "  Instalacion fallida. Revisa el error anterior.",
            )
            return False
    except subprocess.TimeoutExpired:
        p(
            "  Installation timed out (300s). Check your internet connection.",
            "  Instalacion excedio tiempo (300s). Verifica tu conexion a internet.",
        )
        return False
    except Exception as e:
        p(
            f"  Installation error: {e}",
            f"  Error de instalacion: {e}",
        )
        return False


# ── Step 3: Run setup wizard ──

def run_wizard() -> bool:
    """Run the Marlow setup wizard via subprocess."""
    header(
        "Step 3/4: Running setup wizard...",
        "Paso 3/4: Ejecutando wizard de configuracion...",
    )

    try:
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from marlow.core.setup_wizard import run_setup_wizard; run_setup_wizard()",
            ],
            timeout=180,
        )
        if result.returncode == 0:
            p(
                "  Setup wizard completed!",
                "  Wizard de configuracion completado!",
            )
            return True
        else:
            p(
                "  Setup wizard had issues (non-fatal).",
                "  El wizard tuvo problemas (no fatales).",
            )
            return True  # Non-fatal — wizard handles errors internally
    except subprocess.TimeoutExpired:
        p(
            "  Setup wizard timed out. You can run it later by deleting ~/.marlow/setup_complete.json",
            "  Wizard excedio tiempo. Puedes ejecutarlo despues borrando ~/.marlow/setup_complete.json",
        )
        return True
    except Exception as e:
        p(
            f"  Setup wizard error: {e}",
            f"  Error del wizard: {e}",
        )
        return True  # Non-fatal


# ── Step 4: Detect and configure MCP clients ──

# Known MCP client config paths (relative to %APPDATA%)
_MCP_CLIENT_CONFIGS = [
    # Claude Desktop
    ("Claude Desktop", Path("Claude") / "claude_desktop_config.json"),
    # Cursor
    ("Cursor", Path(".cursor") / "mcp.json"),
]


def detect_mcp_clients() -> None:
    """Find MCP clients and offer to add Marlow config."""
    header(
        "Step 4/4: Detecting MCP clients...",
        "Paso 4/4: Detectando clientes MCP...",
    )

    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        p(
            "  Could not find %APPDATA%. Skipping MCP client detection.",
            "  No se encontro %APPDATA%. Omitiendo deteccion de clientes MCP.",
        )
        return

    home = Path.home()
    found_any = False

    for client_name, rel_path in _MCP_CLIENT_CONFIGS:
        # Check both %APPDATA% and home directory
        candidates = [
            Path(appdata) / rel_path,
            home / rel_path,
        ]

        for config_path in candidates:
            if not config_path.exists():
                continue

            found_any = True
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Check if mcpServers key exists
                if "mcpServers" not in data:
                    data["mcpServers"] = {}

                # Never overwrite existing Marlow entry
                if "marlow" in data["mcpServers"]:
                    p(
                        f"  {client_name}: Marlow already configured at {config_path}",
                        f"  {client_name}: Marlow ya configurado en {config_path}",
                    )
                    continue

                # Add Marlow entry
                data["mcpServers"]["marlow"] = {
                    "command": "marlow",
                }

                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                p(
                    f"  {client_name}: Marlow added to {config_path}",
                    f"  {client_name}: Marlow agregado a {config_path}",
                )

            except (json.JSONDecodeError, PermissionError) as e:
                p(
                    f"  {client_name}: Could not update config ({e})",
                    f"  {client_name}: No se pudo actualizar config ({e})",
                )

    if not found_any:
        p(
            "  No MCP clients found. You can manually add Marlow to your client's config:",
            "  No se encontraron clientes MCP. Puedes agregar Marlow manualmente a tu cliente:",
        )
        print('    { "mcpServers": { "marlow": { "command": "marlow" } } }')
        print()


# ── Main ──

def main():
    """Run the Marlow installer."""
    print()
    print("  __  __            _")
    print(" |  \\/  | __ _ _ __| | _____      __")
    print(" | |\\/| |/ _` | '__| |/ _ \\ \\ /\\ / /")
    print(" | |  | | (_| | |  | | (_) \\ V  V /")
    print(" |_|  |_|\\__,_|_|  |_|\\___/ \\_/\\_/")
    print()
    p(
        "AI that works beside you, not instead of you",
        "IA que trabaja a tu lado, no en tu lugar",
    )

    # Step 1
    if not check_python():
        sys.exit(1)

    # Step 2
    if not install_marlow():
        sys.exit(1)

    # Step 3
    run_wizard()

    # Step 4
    detect_mcp_clients()

    # ── Final ──
    print()
    print("=" * 60)
    p(
        "  Installation complete!",
        "  Instalacion completa!",
    )
    p(
        "  Restart your MCP client to start using Marlow.",
        "  Reinicia tu cliente MCP para empezar a usar Marlow.",
    )
    p(
        "  Run 'marlow' in terminal to start manually.",
        "  Ejecuta 'marlow' en la terminal para iniciar manualmente.",
    )
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
