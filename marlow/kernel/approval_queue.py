"""ApprovalQueue — Manages approval of proactive Tier 2+ actions.

Queues suggestions, notifies the user via multiple channels (mako,
sidebar WS, voice), and waits for explicit approval/rejection/timeout.
Timeout always cancels — never executes by default.

/ Cola de aprobación — gestiona acciones proactivas que requieren OK del usuario.
"""

import asyncio
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("marlow.kernel.approval_queue")


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class PendingApproval:
    """A proactive action awaiting user approval."""
    id: str
    pattern_id: str
    tool_name: str
    params: dict
    trust_level: int
    description: str
    confidence: float = 0.0
    created_at: float = 0.0           # time.time()
    timeout_seconds: float = 60.0
    status: ApprovalStatus = ApprovalStatus.PENDING

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.timeout_seconds

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pattern_id": self.pattern_id,
            "tool_name": self.tool_name,
            "params": self.params,
            "trust_level": self.trust_level,
            "description": self.description,
            "confidence": self.confidence,
            "status": self.status.value,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ApprovalResult:
    """Outcome of a submitted approval request."""
    approved: bool
    status: ApprovalStatus
    approval_id: str = ""
    error: str = ""


class ApprovalQueue:
    """Queues proactive actions and waits for user approval.

    Integration points:
    - Mako notifications (notify-send)
    - Sidebar WebSocket (broadcast approval_request)
    - Voice TTS (future)

    Timeout always cancels — never auto-executes.
    """

    def __init__(
        self,
        pipeline: Any = None,
        pattern_detector: Any = None,
        ws_broadcast: Optional[Callable] = None,
        default_timeout: float = 60.0,
    ):
        self._pipeline = pipeline
        self._detector = pattern_detector
        self._ws_broadcast = ws_broadcast
        self._default_timeout = default_timeout

        # Pending approvals: id -> (PendingApproval, asyncio.Event)
        self._pending: dict[str, tuple[PendingApproval, asyncio.Event]] = {}

    # ── Public API ───────────────────────────────────────────

    async def submit(
        self,
        tool_name: str,
        params: dict,
        pattern_id: str,
        trust_level: int,
        description: str = "",
        confidence: float = 0.0,
        timeout: Optional[float] = None,
    ) -> ApprovalResult:
        """Submit an action for approval. Blocks until approved/rejected/timeout."""
        approval_id = uuid.uuid4().hex[:12]
        timeout_s = timeout or self._default_timeout

        approval = PendingApproval(
            id=approval_id,
            pattern_id=pattern_id,
            tool_name=tool_name,
            params=params,
            trust_level=trust_level,
            description=description or f"Ejecutar {tool_name}",
            confidence=confidence,
            created_at=time.time(),
            timeout_seconds=timeout_s,
        )

        event = asyncio.Event()
        self._pending[approval_id] = (approval, event)

        # Notify user via all channels
        self._notify_user(approval)

        # Wait for response or timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            approval.status = ApprovalStatus.EXPIRED
            logger.info("Approval %s expired (timeout=%ds)", approval_id, timeout_s)
        except asyncio.CancelledError:
            approval.status = ApprovalStatus.CANCELLED
        finally:
            self._pending.pop(approval_id, None)

        # Process result
        if approval.status == ApprovalStatus.APPROVED:
            return await self._execute_approved(approval)
        elif approval.status == ApprovalStatus.REJECTED:
            if self._detector:
                self._detector.record_feedback(pattern_id, approved=False)
            return ApprovalResult(
                approved=False,
                status=ApprovalStatus.REJECTED,
                approval_id=approval_id,
            )
        else:
            # Expired or cancelled — never execute
            return ApprovalResult(
                approved=False,
                status=approval.status,
                approval_id=approval_id,
            )

    def approve(self, approval_id: str) -> bool:
        """Approve a pending request (called from HTTP/WS/bridge)."""
        entry = self._pending.get(approval_id)
        if not entry:
            return False
        approval, event = entry
        if approval.status != ApprovalStatus.PENDING:
            return False
        approval.status = ApprovalStatus.APPROVED
        event.set()
        logger.info("Approval %s approved by user", approval_id)
        return True

    def reject(self, approval_id: str) -> bool:
        """Reject a pending request (called from HTTP/WS/bridge)."""
        entry = self._pending.get(approval_id)
        if not entry:
            return False
        approval, event = entry
        if approval.status != ApprovalStatus.PENDING:
            return False
        approval.status = ApprovalStatus.REJECTED
        event.set()
        logger.info("Approval %s rejected by user", approval_id)
        return True

    def get_pending(self) -> list[dict]:
        """Return all pending approvals (for UI)."""
        result = []
        for approval, _ in self._pending.values():
            if approval.status == ApprovalStatus.PENDING and not approval.is_expired:
                result.append(approval.to_dict())
        return result

    def cancel_all(self):
        """Cancel all pending approvals (kill switch)."""
        for approval, event in list(self._pending.values()):
            approval.status = ApprovalStatus.CANCELLED
            event.set()
        logger.info("All %d pending approvals cancelled", len(self._pending))

    # ── Internal ─────────────────────────────────────────────

    async def _execute_approved(self, approval: PendingApproval) -> ApprovalResult:
        """Execute an approved action via pipeline."""
        if self._detector:
            self._detector.record_feedback(approval.pattern_id, approved=True)

        if not self._pipeline:
            return ApprovalResult(
                approved=True,
                status=ApprovalStatus.APPROVED,
                approval_id=approval.id,
                error="no pipeline available",
            )

        try:
            result = await self._pipeline.execute(
                approval.tool_name,
                approval.params,
                origin="proactive",
            )
            return ApprovalResult(
                approved=True,
                status=ApprovalStatus.APPROVED,
                approval_id=approval.id,
                error="" if result.success else result.error,
            )
        except Exception as e:
            return ApprovalResult(
                approved=True,
                status=ApprovalStatus.APPROVED,
                approval_id=approval.id,
                error=str(e),
            )

    def _notify_user(self, approval: PendingApproval):
        """Notify user via all available channels."""
        msg = (
            f"Marlow sugiere: {approval.description} "
            f"(confianza: {approval.confidence:.0%}). "
            f"Aprueba con 'marlow approve' o ignora."
        )

        # Mako notification
        try:
            subprocess.run(
                ["notify-send", "-a", "Marlow", "-u", "normal", "Marlow", msg],
                capture_output=True, timeout=3,
            )
        except Exception:
            pass

        # Sidebar WebSocket
        if self._ws_broadcast:
            try:
                self._ws_broadcast({
                    "type": "approval_request",
                    "id": approval.id,
                    "tool_name": approval.tool_name,
                    "description": approval.description,
                    "confidence": approval.confidence,
                    "timeout": approval.timeout_seconds,
                })
            except Exception:
                pass
