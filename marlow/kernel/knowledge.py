"""App Knowledge Base manager — learns about applications over time.

Wraps KnowledgeRepository with business logic: deciding best input
methods, merging error solutions, importing existing data, and
caching discovered elements and dialogs.
"""

from __future__ import annotations

import json
from typing import Optional

from .db.repositories import AppKnowledge, ErrorPattern, KnowledgeRepository


class AppKnowledgeManager:
    """High-level interface for app knowledge operations.

    Parameters
    ----------
    * **knowledge_repo** (KnowledgeRepository):
        Repository backed by state.db for persistent storage.
    """

    def __init__(self, knowledge_repo: KnowledgeRepository):
        self._repo = knowledge_repo

    # ── Query methods (what the Kernel asks) ──

    async def get_app_info(self, app_name: str) -> Optional[AppKnowledge]:
        """Get everything we know about an app."""
        return await self._repo.get_app(app_name)

    async def get_preferred_input(self, app_name: str) -> str:
        """What input method works best for this app? Default: 'uia'."""
        app = await self._repo.get_app(app_name)
        return app.preferred_input if app else "uia"

    async def get_reliability(self, app_name: str) -> float:
        """How reliable is automation for this app? 0.0-1.0."""
        app = await self._repo.get_app(app_name)
        return app.reliability if app else 0.5

    async def should_use_cdp(
        self, app_name: str
    ) -> tuple[bool, Optional[int]]:
        """Should we use CDP for this app? Returns (should_use, port)."""
        app = await self._repo.get_app(app_name)
        if not app:
            return False, None
        if app.framework in ("electron", "cef") and app.cdp_port:
            return True, app.cdp_port
        return False, None

    async def get_known_elements(self, app_name: str) -> dict:
        """Get cached element info for faster automation."""
        app = await self._repo.get_app(app_name)
        return app.known_elements if app else {}

    async def get_error_solution(
        self, app_name: str, tool_name: str, error_type: str
    ) -> Optional[str]:
        """Find a known working solution for this error."""
        return await self._repo.get_error_solution(
            app_name, tool_name, error_type,
        )

    # ── Learning methods (called after actions) ──

    async def record_action(
        self,
        app_name: str,
        success: bool,
        framework: str | None = None,
        cdp_port: int | None = None,
    ) -> None:
        """Record an action result. Updates reliability score."""
        app = await self._repo.get_app(app_name)
        if not app:
            app = AppKnowledge(
                app_name=app_name,
                framework=framework,
                cdp_port=cdp_port,
            )
            await self._repo.upsert_app(app)
        elif framework and not app.framework:
            app.framework = framework
            if framework in ("electron", "cef") and cdp_port:
                app.cdp_port = cdp_port
            await self._repo.upsert_app(app)

        await self._repo.record_action_result(app_name, success)

    async def record_error(
        self,
        app_name: str,
        tool_name: str,
        error_type: str,
        error_message: str | None = None,
        solution: str | None = None,
        worked: bool = False,
    ) -> None:
        """Record an error pattern and optional solution."""
        pattern = ErrorPattern(
            app_name=app_name,
            tool_name=tool_name,
            error_type=error_type,
            error_message=error_message,
            solution=solution,
            solution_worked=worked,
        )
        await self._repo.record_error(pattern)

    async def learn_element(
        self, app_name: str, element_name: str, element_info: dict
    ) -> None:
        """Cache a discovered element for future use."""
        app = await self._repo.get_app(app_name)
        if not app:
            app = AppKnowledge(app_name=app_name)

        app.known_elements[element_name] = element_info
        await self._repo.upsert_app(app)

    async def learn_dialog(
        self, app_name: str, dialog_info: dict
    ) -> None:
        """Record how a dialog was handled for future reference."""
        app = await self._repo.get_app(app_name)
        if not app:
            app = AppKnowledge(app_name=app_name)

        # Deduplicate by title
        title = dialog_info.get("title", "")
        app.known_dialogs = [
            d for d in app.known_dialogs if d.get("title") != title
        ]
        app.known_dialogs.append(dialog_info)
        await self._repo.upsert_app(app)

    # ── Bulk import from existing data ──

    async def import_from_error_journal(
        self, journal_data: list[dict]
    ) -> int:
        """Import existing error_journal entries into the KB.

        Returns the number of entries imported.
        """
        count = 0
        for entry in journal_data:
            await self.record_error(
                app_name=entry.get("app_name", "unknown"),
                tool_name=entry.get("tool_name", "unknown"),
                error_type=entry.get("error_type", "unknown"),
                error_message=entry.get("error_message"),
                solution=entry.get("solution"),
                worked=entry.get("worked", False),
            )
            count += 1
        return count

    async def import_from_cdp_knowledge(
        self, cdp_data: dict
    ) -> int:
        """Import existing cdp_knowledge.json into the KB.

        Expected format: ``{app_name: {port: int, framework: str, ...}}``

        Returns the number of apps imported.
        """
        count = 0
        for app_name, info in cdp_data.items():
            app = await self._repo.get_app(app_name)
            if app is None:
                app = AppKnowledge(app_name=app_name)

            app.framework = info.get("framework", app.framework)
            app.cdp_port = info.get("port", app.cdp_port)
            if app.cdp_port:
                app.preferred_input = "cdp"
            await self._repo.upsert_app(app)
            count += 1
        return count
