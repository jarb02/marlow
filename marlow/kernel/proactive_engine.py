"""ProactiveEngine — Acts on detected patterns when user is idle.

Listens for system.user_idle events, evaluates due patterns against
SecurityGate, DesktopObserver state, and confidence thresholds, then
either auto-executes (Tier 0-1 + high confidence), suggests via
notification (Tier 2 or medium confidence), or skips.

/ Motor proactivo — actúa cuando detecta patrones y el usuario está idle.
"""

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("marlow.kernel.proactive_engine")


# ── Classification ───────────────────────────────────────────

class ActionClass:
    AUTO = "auto"        # Execute silently
    SUGGEST = "suggest"  # Notify user, wait for input
    SKIP = "skip"        # Log only


# ── Configuration defaults ───────────────────────────────────

@dataclass
class ProactiveConfig:
    enabled: bool = True
    cooldown_seconds: float = 120.0
    max_per_hour: int = 5
    max_per_day: int = 20
    idle_minutes: float = 5.0
    confidence_threshold: float = 0.9
    max_consecutive_failures: int = 3
    pause_after_failures_minutes: float = 60.0


# ── Pending suggestions for implicit approval ───────────────

@dataclass
class PendingSuggestion:
    pattern_id: str
    tool_name: str
    params: dict
    suggested_at: float  # time.time()


class ProactiveEngine:
    """Evaluates patterns and acts proactively when user is idle.

    Subscribes to EventBus:
    - system.user_idle: triggers pattern evaluation
    - system.user_active: cancels pending evaluations
    - action.completed: implicit approval of pending suggestions
    """

    def __init__(
        self,
        pattern_detector: Any = None,
        desktop_observer: Any = None,
        pipeline: Any = None,
        security_gate: Any = None,
        event_bus: Any = None,
        config: Optional[ProactiveConfig] = None,
    ):
        self._detector = pattern_detector
        self._observer = desktop_observer
        self._pipeline = pipeline
        self._gate = security_gate
        self._event_bus = event_bus
        self._config = config or ProactiveConfig()
        self._approval_queue = None  # set externally or via setter
        self._rollback = None        # RollbackExecutor, set externally

        # State
        self._paused = False
        self._user_idle = False
        self._last_action_time: float = 0.0
        self._actions_this_hour: list[float] = []
        self._actions_today: list[float] = []
        self._consecutive_failures: int = 0
        self._pause_until: float = 0.0
        self._pending_suggestions: dict[str, PendingSuggestion] = {}
        self._executed_today: set[str] = set()  # pattern IDs

        # Stats
        self._total_auto: int = 0
        self._total_suggest: int = 0
        self._total_skip: int = 0
        self._total_failures: int = 0

        # Lifecycle
        self._task: Optional[asyncio.Task] = None
        self._eval_task: Optional[asyncio.Task] = None
        self._stopping = False

    # ── Public API ───────────────────────────────────────────

    def pause(self):
        self._paused = True
        logger.info("ProactiveEngine paused")

    def resume(self):
        self._paused = False
        logger.info("ProactiveEngine resumed")

    def toggle(self):
        """Toggle proactivity on/off (kill switch)."""
        if self._paused:
            self.resume()
        else:
            self.pause()
            # Cancel all pending approvals
            if self._approval_queue:
                self._approval_queue.cancel_all()
            # Rollback last action if recent
            if self._rollback:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._rollback.rollback_last())
                except RuntimeError:
                    pass

    def is_paused(self) -> bool:
        return self._paused

    def get_stats(self) -> dict:
        now = time.time()
        self._trim_rate_limits(now)
        return {
            "enabled": self._config.enabled,
            "paused": self._paused,
            "user_idle": self._user_idle,
            "actions_this_hour": len(self._actions_this_hour),
            "actions_today": len(self._actions_today),
            "consecutive_failures": self._consecutive_failures,
            "total_auto": self._total_auto,
            "total_suggest": self._total_suggest,
            "total_skip": self._total_skip,
            "total_failures": self._total_failures,
            "active_patterns": (
                len(self._detector.get_active_patterns())
                if self._detector else 0
            ),
            "pending_suggestions": len(self._pending_suggestions),
        }

    async def run(self):
        """Background task: subscribe to events and handle idle triggers."""
        logger.info("ProactiveEngine starting (enabled=%s)", self._config.enabled)

        # Subscribe to events
        if self._event_bus:
            self._event_bus.subscribe(
                "system.user_idle", self._on_user_idle, "proactive_engine",
            )
            self._event_bus.subscribe(
                "system.user_active", self._on_user_active, "proactive_engine",
            )
            self._event_bus.subscribe(
                "action.completed", self._on_action_completed, "proactive_engine",
            )
            self._event_bus.subscribe(
                "system.proactivity_toggle", self._on_proactivity_toggle, "proactive_engine",
            )

        # Keep alive until stopped
        while not self._stopping:
            try:
                await asyncio.sleep(60)
                # Periodic cleanup of old suggestions (>10 min)
                self._cleanup_suggestions()
            except asyncio.CancelledError:
                break

        logger.info("ProactiveEngine stopped")

    def stop(self):
        """Signal shutdown."""
        self._stopping = True
        if self._eval_task and not self._eval_task.done():
            self._eval_task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()

    # ── EventBus handlers ────────────────────────────────────

    async def _on_user_idle(self, event):
        """Triggered when DesktopObserver detects user idle."""
        self._user_idle = True
        if not self._should_evaluate():
            return

        # Start evaluation in background (cancellable if user returns)
        if self._eval_task and not self._eval_task.done():
            return  # already evaluating

        self._eval_task = asyncio.ensure_future(self._evaluate_patterns())

    async def _on_user_active(self, event):
        """Triggered when user returns from idle."""
        self._user_idle = False
        # Cancel any pending evaluation
        if self._eval_task and not self._eval_task.done():
            self._eval_task.cancel()
            logger.debug("Cancelled proactive evaluation — user active")

    async def _on_action_completed(self, event):
        """Check for implicit approval of pending suggestions."""
        if not self._pending_suggestions:
            return

        tool = getattr(event, "tool_name", "")
        if not tool:
            return

        now = time.time()
        matched = None

        for sid, suggestion in self._pending_suggestions.items():
            if suggestion.tool_name == tool:
                # Same tool within 10 min of suggestion
                if now - suggestion.suggested_at < 600:
                    matched = sid
                    break

        if matched:
            suggestion = self._pending_suggestions.pop(matched)
            if self._detector:
                self._detector.record_feedback(suggestion.pattern_id, approved=True)
                logger.info(
                    "Implicit approval: user executed %s (pattern %s)",
                    tool, suggestion.pattern_id,
                )

    async def _on_proactivity_toggle(self, event):
        """Handle Super+Escape kill switch from compositor."""
        self.toggle()
        state_msg = "pausada" if self._paused else "reanudada"
        self._notify(f"Proactividad {state_msg}. Super+Escape para cambiar.")

    # ── Core evaluation ──────────────────────────────────────

    def _should_evaluate(self) -> bool:
        """Check if proactive evaluation is allowed right now."""
        if not self._config.enabled:
            return False
        if self._paused:
            return False
        if not self._detector or not self._pipeline:
            return False
        if not self._user_idle:
            return False

        now = time.time()

        # Check failure pause
        if now < self._pause_until:
            logger.debug("Proactivity paused until %s", datetime.fromtimestamp(self._pause_until))
            return False

        # Check cooldown
        if now - self._last_action_time < self._config.cooldown_seconds:
            return False

        # Check rate limits
        self._trim_rate_limits(now)
        if len(self._actions_this_hour) >= self._config.max_per_hour:
            return False
        if len(self._actions_today) >= self._config.max_per_day:
            return False

        return True

    async def _evaluate_patterns(self):
        """Evaluate due patterns and act on them."""
        try:
            now = datetime.now()
            due = self._detector.get_due_patterns(now)

            if not due:
                return

            logger.info("Evaluating %d due patterns", len(due))

            for pattern in due:
                if self._stopping or not self._user_idle:
                    break

                # Skip if already executed today
                if pattern.id in self._executed_today:
                    continue

                await self._process_pattern(pattern)

                # Respect cooldown between actions
                await asyncio.sleep(2.0)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Pattern evaluation error: %s", e)

    async def _process_pattern(self, pattern):
        """Classify and process a single pattern."""
        classification = await self._classify(pattern)

        if classification == ActionClass.AUTO:
            await self._execute_auto(pattern)
        elif classification == ActionClass.SUGGEST:
            await self._execute_suggest(pattern)
        else:
            self._total_skip += 1
            logger.debug(
                "SKIP pattern %s (%s, confidence=%.2f)",
                pattern.id, pattern.tool_name, pattern.confidence,
            )

    async def _classify(self, pattern) -> str:
        """Classify pattern as AUTO / SUGGEST / SKIP."""
        # Check security gate for trust level
        trust_level = 99  # default: unknown = skip
        if self._gate:
            try:
                result = await self._gate.check(
                    pattern.tool_name,
                    pattern.params,
                    origin="proactive",
                )
                trust_level = result.trust_level

                # If security gate blocks entirely, skip
                if not result.allowed and not result.suggestion_only:
                    return ActionClass.SKIP
            except Exception as e:
                logger.debug("SecurityGate check error: %s", e)
                return ActionClass.SKIP

        # Check desktop state
        if self._observer:
            state = self._observer.get_state()
            # Don't act in unstable desktop
            if state.desktop_state in ("INESTABLE", "TORMENTA"):
                return ActionClass.SKIP

            # Check if action already done (e.g., Firefox already open)
            if self._is_already_done(pattern, state):
                return ActionClass.SKIP

        # Classification based on confidence + trust level
        if pattern.confidence >= 0.95 and trust_level <= 1:
            return ActionClass.AUTO
        elif pattern.confidence >= 0.90 and trust_level <= 2:
            return ActionClass.SUGGEST
        else:
            return ActionClass.SKIP

    def _is_already_done(self, pattern, state) -> bool:
        """Check if a pattern's action is already satisfied."""
        # For open_application / launch patterns, check if app is already open
        if pattern.tool_name in ("open_application", "launch_in_shadow"):
            app_name = (
                pattern.params.get("application", "")
                or pattern.params.get("command", "")
            ).lower()
            if app_name:
                for w in state.windows.values():
                    if app_name in w.app_id.lower() or app_name in w.title.lower():
                        return True
        return False

    # ── Execution ────────────────────────────────────────────

    async def _execute_auto(self, pattern):
        """Execute a pattern automatically (silent, log only)."""
        now = time.time()
        logger.info(
            "AUTO executing pattern %s: %s %s",
            pattern.id, pattern.tool_name, pattern.params,
        )

        # Shadow-first: rewrite open_application to launch_in_shadow
        exec_tool = pattern.tool_name
        exec_params = dict(pattern.params)
        if exec_tool == "open_application":
            app = exec_params.pop("application", "")
            exec_tool = "launch_in_shadow"
            exec_params = {"command": app} if app else exec_params

        # Record pre-action state for rollback
        if self._rollback:
            self._rollback.record_action(exec_tool, exec_params)

        try:
            result = await self._pipeline.execute(
                exec_tool,
                exec_params,
                origin="proactive",
            )

            if result.success:
                self._record_action(now)
                self._executed_today.add(pattern.id)
                self._consecutive_failures = 0
                self._total_auto += 1

                if self._detector:
                    self._detector.record_feedback(pattern.id, approved=True)

                # Notify only if visible to user (not shadow space)
                if exec_tool not in ("launch_in_shadow",):
                    self._notify(
                        f"Ejecuté {pattern.tool_name} automáticamente "
                        f"(patrón detectado, confianza {pattern.confidence:.0%})"
                    )
            else:
                self._on_proactive_failure(pattern, result.error)

        except Exception as e:
            self._on_proactive_failure(pattern, str(e))

    async def _execute_suggest(self, pattern):
        """Submit suggestion via ApprovalQueue or fallback to notification."""
        logger.info(
            "SUGGEST pattern %s: %s (confidence=%.2f)",
            pattern.id, pattern.tool_name, pattern.confidence,
        )

        if self._approval_queue:
            # Use ApprovalQueue for explicit approve/reject
            try:
                result = await self._approval_queue.submit(
                    tool_name=pattern.tool_name,
                    params=pattern.params,
                    pattern_id=pattern.id,
                    trust_level=2,
                    description=self._describe_pattern(pattern),
                    confidence=pattern.confidence,
                )
                if result.approved:
                    self._record_action(time.time())
                    self._consecutive_failures = 0
            except Exception as e:
                logger.warning("ApprovalQueue error: %s", e)
        else:
            # Fallback: notify-send only (no approval flow)
            self._notify(
                f"Parece que es hora de {self._describe_pattern(pattern)}. "
                f"¿Lo hago? (confianza: {pattern.confidence:.0%})"
            )
            # Track as pending for implicit approval
            self._pending_suggestions[pattern.id] = PendingSuggestion(
                pattern_id=pattern.id,
                tool_name=pattern.tool_name,
                params=pattern.params,
                suggested_at=time.time(),
            )

        self._total_suggest += 1
        self._executed_today.add(pattern.id)

    def _describe_pattern(self, pattern) -> str:
        """Human-readable description of what the pattern does."""
        tool = pattern.tool_name
        if tool == "open_application":
            app = pattern.params.get("application", "una aplicación")
            return f"abrir {app}"
        elif tool == "focus_window":
            win = pattern.params.get("window_title", "una ventana")
            return f"enfocar {win}"
        elif tool == "launch_in_shadow":
            cmd = pattern.params.get("command", "un comando")
            return f"lanzar {cmd} en shadow"
        return f"ejecutar {tool}"

    # ── Rate limiting & failure handling ──────────────────────

    def _record_action(self, timestamp: float):
        """Record that a proactive action was taken."""
        self._actions_this_hour.append(timestamp)
        self._actions_today.append(timestamp)
        self._last_action_time = timestamp

    def _trim_rate_limits(self, now: float):
        """Remove expired rate limit entries."""
        hour_ago = now - 3600
        day_ago = now - 86400
        self._actions_this_hour = [t for t in self._actions_this_hour if t > hour_ago]
        self._actions_today = [t for t in self._actions_today if t > day_ago]

    def _on_proactive_failure(self, pattern, error: str):
        """Handle failure of a proactive action."""
        self._consecutive_failures += 1
        self._total_failures += 1
        logger.warning(
            "Proactive action failed (%d consecutive): %s — %s",
            self._consecutive_failures, pattern.tool_name, error,
        )

        if self._consecutive_failures >= self._config.max_consecutive_failures:
            pause_seconds = self._config.pause_after_failures_minutes * 60
            self._pause_until = time.time() + pause_seconds
            logger.warning(
                "Proactivity paused for %.0f min after %d consecutive failures",
                self._config.pause_after_failures_minutes,
                self._consecutive_failures,
            )

    # ── Notifications ────────────────────────────────────────

    @staticmethod
    def _notify(message: str):
        """Send desktop notification via notify-send/mako."""
        try:
            subprocess.run(
                ["notify-send", "-a", "Marlow", "-u", "normal", "Marlow", message],
                capture_output=True,
                timeout=3,
            )
        except Exception:
            pass  # non-critical

    # ── Cleanup ──────────────────────────────────────────────

    def _cleanup_suggestions(self):
        """Remove pending suggestions older than 10 minutes."""
        now = time.time()
        expired = [
            sid for sid, s in self._pending_suggestions.items()
            if now - s.suggested_at > 600
        ]
        for sid in expired:
            del self._pending_suggestions[sid]

        # Reset daily counters at midnight
        if self._actions_today and datetime.now().hour == 0:
            cutoff = time.time() - 86400
            self._actions_today = [t for t in self._actions_today if t > cutoff]
            self._executed_today.clear()
