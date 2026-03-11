"""LLM Adapter functions for the Universal Tool Registry.

Generate format-specific tool declarations from the single TOOL_REGISTRY
for each LLM provider. Currently supports Gemini and Anthropic formats.

Usage::

    from marlow.kernel.adapters import to_gemini, to_anthropic, get_tool_names

    # All tools for Gemini
    gemini_tools = to_gemini()

    # Only input + windows tools, excluding kill_switch
    gemini_subset = to_gemini(categories=["input", "windows"], exclude=["kill_switch"])

    # Anthropic format for accessibility tools
    anthropic_tools = to_anthropic(categories=["accessibility"])

    # Just the names
    names = get_tool_names(categories=["cdp"])

/ Adaptadores LLM para el registro universal de herramientas.
"""

from __future__ import annotations

from typing import Any, Optional

from .registry import TOOL_REGISTRY


# ─────────────────────────────────────────────────────────────
# Internal: Filtering
# ─────────────────────────────────────────────────────────────

def _filter_tools(
    categories: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
) -> dict[str, dict[str, Any]]:
    """Filter TOOL_REGISTRY by categories and exclude list.

    Args:
        categories: List of category strings to include. None means all.
        exclude: List of tool names to exclude. None means no exclusions.

    Returns:
        Filtered dict of tool_name -> tool_spec.
    """
    result = {}
    exclude_set = set(exclude) if exclude else set()

    for name, spec in TOOL_REGISTRY.items():
        if name in exclude_set:
            continue
        if categories is not None and spec["category"] not in categories:
            continue
        result[name] = spec

    return result


# ─────────────────────────────────────────────────────────────
# Gemini Adapter
# ─────────────────────────────────────────────────────────────

# Mapping from registry type strings to Gemini Type enum values.
_GEMINI_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def to_gemini(
    categories: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
) -> list:
    """Generate Gemini FunctionDeclaration list from the tool registry.

    Lazily imports google.genai.types to avoid hard dependency.

    Args:
        categories: List of category strings to include. None means all.
        exclude: List of tool names to exclude.

    Returns:
        List of google.genai.types.FunctionDeclaration objects.
    """
    from google.genai import types

    Type = types.Type

    type_enum_map = {
        "string": Type.STRING,
        "integer": Type.INTEGER,
        "number": Type.NUMBER,
        "boolean": Type.BOOLEAN,
        "array": Type.ARRAY,
        "object": Type.OBJECT,
    }

    tools = _filter_tools(categories, exclude)
    declarations = []

    for name, spec in tools.items():
        params = spec.get("params", {})
        required_list = spec.get("required", [])

        if not params:
            # No parameters -- empty object schema
            schema = types.Schema(
                type=Type.OBJECT,
                properties={},
            )
        else:
            properties = {}
            for param_name, param_spec in params.items():
                param_type_str = param_spec.get("type", "string")
                param_type = type_enum_map.get(param_type_str, Type.STRING)
                param_desc = param_spec.get("description", "")

                if param_type_str == "array":
                    # Array params: items are strings by default
                    properties[param_name] = types.Schema(
                        type=Type.ARRAY,
                        description=param_desc,
                        items=types.Schema(type=Type.STRING),
                    )
                elif param_type_str == "object":
                    # Object params: generic object
                    properties[param_name] = types.Schema(
                        type=Type.OBJECT,
                        description=param_desc,
                    )
                else:
                    properties[param_name] = types.Schema(
                        type=param_type,
                        description=param_desc,
                    )

            # Build required list: only include params that actually exist
            required = [r for r in required_list if r in properties]

            schema_kwargs = {
                "type": Type.OBJECT,
                "properties": properties,
            }
            if required:
                schema_kwargs["required"] = required

            schema = types.Schema(**schema_kwargs)

        decl = types.FunctionDeclaration(
            name=name,
            description=spec["description"],
            parameters=schema,
        )
        declarations.append(decl)

    return declarations


# ─────────────────────────────────────────────────────────────
# Anthropic Adapter
# ─────────────────────────────────────────────────────────────

def to_anthropic(
    categories: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Generate Anthropic tool definition list from the tool registry.

    Follows the Anthropic API format:
    https://docs.anthropic.com/en/docs/build-with-claude/tool-use

    Args:
        categories: List of category strings to include. None means all.
        exclude: List of tool names to exclude.

    Returns:
        List of dicts, each with: name, description, input_schema.
    """
    tools = _filter_tools(categories, exclude)
    declarations = []

    for name, spec in tools.items():
        params = spec.get("params", {})
        required_list = spec.get("required", [])

        properties = {}
        for param_name, param_spec in params.items():
            param_type_str = param_spec.get("type", "string")
            param_desc = param_spec.get("description", "")

            prop: dict[str, Any] = {
                "type": param_type_str,
                "description": param_desc,
            }

            # Add default if present
            if "default" in param_spec:
                prop["default"] = param_spec["default"]

            # Array items
            if param_type_str == "array":
                prop["items"] = {"type": "string"}

            # Enum values (not stored in registry but could be added)
            # For now, enums are described in the description string.

            properties[param_name] = prop

        # Only include required fields that exist in properties
        required = [r for r in required_list if r in properties]

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required

        declarations.append({
            "name": name,
            "description": spec["description"],
            "input_schema": input_schema,
        })

    return declarations


# ─────────────────────────────────────────────────────────────
# MCP Adapter
# ─────────────────────────────────────────────────────────────

def to_mcp(
    categories: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Generate MCP Tool definition list from the tool registry.

    Follows the MCP SDK Tool format with name, description, inputSchema.

    Args:
        categories: List of category strings to include. None means all.
        exclude: List of tool names to exclude.

    Returns:
        List of dicts compatible with mcp.types.Tool constructor kwargs.
    """
    tools = _filter_tools(categories, exclude)
    declarations = []

    for name, spec in tools.items():
        params = spec.get("params", {})
        required_list = spec.get("required", [])

        properties = {}
        for param_name, param_spec in params.items():
            param_type_str = param_spec.get("type", "string")
            param_desc = param_spec.get("description", "")

            prop: dict[str, Any] = {
                "type": param_type_str,
                "description": param_desc,
            }

            if "default" in param_spec:
                prop["default"] = param_spec["default"]

            if param_type_str == "array":
                prop["items"] = {"type": "string"}

            if param_type_str == "object":
                # Region-style objects get sub-properties
                if param_name == "region":
                    prop["properties"] = {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    }

            properties[param_name] = prop

        required = [r for r in required_list if r in properties]

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required

        declarations.append({
            "name": name,
            "description": spec["description"],
            "inputSchema": input_schema,
        })

    return declarations


# ─────────────────────────────────────────────────────────────
# Name-only query
# ─────────────────────────────────────────────────────────────

def get_tool_names(
    categories: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
) -> list[str]:
    """Return filtered list of tool name strings.

    Args:
        categories: List of category strings to include. None means all.
        exclude: List of tool names to exclude.

    Returns:
        Sorted list of tool name strings.
    """
    tools = _filter_tools(categories, exclude)
    return sorted(tools.keys())


# ─────────────────────────────────────────────────────────────
# Summary / introspection
# ─────────────────────────────────────────────────────────────

def summary() -> dict[str, Any]:
    """Return a summary of the tool registry for debugging.

    Returns:
        Dict with total count, per-category counts, kernel vs MCP-only counts.
    """
    from .registry import get_categories, get_kernel_tools, get_mcp_only_tools

    categories = get_categories()
    per_category = {}
    for cat in categories:
        tools_in_cat = _filter_tools(categories=[cat])
        per_category[cat] = len(tools_in_cat)

    kernel_tools = get_kernel_tools()
    mcp_only = get_mcp_only_tools()

    return {
        "total": len(TOOL_REGISTRY),
        "kernel_registered": len(kernel_tools),
        "mcp_only": len(mcp_only),
        "categories": per_category,
        "alias_count": len(getattr(__import__("marlow.kernel.registry", fromlist=["TOOL_ALIASES"]), "TOOL_ALIASES", {})),
    }
