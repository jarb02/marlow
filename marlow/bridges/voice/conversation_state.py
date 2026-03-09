"""Conversation state machine — multi-turn voice interaction.

Replaces single-turn command/response with a proper FSM that supports
follow-up questions, disambiguation, and natural conversation flow.

/ Maquina de estados para conversacion multi-turno.
"""

from __future__ import annotations

import enum
import logging
import random
import time
from typing import Optional

logger = logging.getLogger("marlow.bridges.voice.conversation_state")


class ConversationState(enum.Enum):
    IDLE = "idle"                        # Wake word listening only
    LISTENING = "listening"              # VAD + ASR active
    PROCESSING = "processing"            # Goal executing
    RESPONDING = "responding"            # TTS speaking result
    FOLLOW_UP = "follow_up"              # Listening WITHOUT wake word
    DISAMBIGUATING = "disambiguating"    # Waiting for user choice
    ERROR = "error"                      # Something went wrong


class ConversationContext:
    """Maintains state across turns of a conversation."""

    def __init__(self):
        self.state = ConversationState.IDLE
        self.user_name: str = ""
        self.current_intent: Optional[str] = None
        self.pending_window_id: Optional[str] = None
        self.last_goal_result: Optional[dict] = None
        self.last_ocr_text: Optional[str] = None
        self.options: list[str] = []
        self.turn_count: int = 0
        self.conversation_start: Optional[float] = None
        self.last_activity: Optional[float] = None
        self.response_channel: Optional[str] = None

    def start_conversation(self, channel: str):
        """Transition from IDLE to LISTENING."""
        self.state = ConversationState.LISTENING
        self.conversation_start = time.time()
        self.last_activity = time.time()
        self.turn_count = 0
        self.response_channel = channel

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def is_expired(self) -> bool:
        """Check if conversation has timed out."""
        if self.state in (ConversationState.IDLE, ConversationState.PROCESSING):
            return False
        if self.last_activity is None:
            return False
        return (time.time() - self.last_activity) > self._get_timeout()

    def _get_timeout(self) -> float:
        """Timeout depends on current state."""
        timeouts = {
            ConversationState.LISTENING: 10.0,
            ConversationState.FOLLOW_UP: 30.0,
            ConversationState.DISAMBIGUATING: 30.0,
            ConversationState.ERROR: 5.0,
            ConversationState.RESPONDING: 60.0,
        }
        return timeouts.get(self.state, 30.0)

    def reset(self):
        """Return to IDLE, clear transient state."""
        self.state = ConversationState.IDLE
        self.current_intent = None
        self.pending_window_id = None
        self.last_goal_result = None
        self.last_ocr_text = None
        self.options = []
        self.turn_count = 0
        self.response_channel = None

    # ── State transitions ──

    def on_wake_word(self, channel: str = "voice"):
        """Wake word detected or Super+V pressed."""
        if self.state == ConversationState.IDLE:
            self.start_conversation(channel)
            logger.info("Conversation started via %s", channel)

    def on_speech_end(self, transcript: str):
        """VAD detected end of speech, ASR produced transcript."""
        if self.state in (ConversationState.LISTENING, ConversationState.FOLLOW_UP):
            self.current_intent = transcript
            self.turn_count += 1
            self.state = ConversationState.PROCESSING
            self.touch()
            logger.info("Turn %d: '%s'", self.turn_count, transcript[:80])

    def on_goal_complete(self, result: dict):
        """Goal execution finished."""
        if self.state == ConversationState.PROCESSING:
            self.last_goal_result = result
            self.state = ConversationState.RESPONDING
            self.touch()

    def on_response_spoken(self, needs_follow_up: bool = False):
        """TTS finished speaking the response."""
        if self.state == ConversationState.RESPONDING:
            if needs_follow_up:
                self.state = ConversationState.FOLLOW_UP
                logger.info("Entering follow-up mode (no wake word needed)")
            else:
                self.reset()
            self.touch()

    def on_disambiguation_needed(self, options: list[str]):
        """Marlow needs user to pick from options."""
        self.options = options
        self.state = ConversationState.DISAMBIGUATING
        self.touch()

    def on_option_selected(self, index: int):
        """User selected an option during disambiguation."""
        if self.state == ConversationState.DISAMBIGUATING:
            if 0 <= index < len(self.options):
                self.current_intent = self.options[index]
                self.state = ConversationState.PROCESSING
                self.touch()

    def on_error(self, error: str):
        """Something went wrong."""
        self.state = ConversationState.ERROR
        self.touch()
        logger.error("Conversation error: %s", error)

    def on_cancel(self):
        """User said "cancelar" or kill switch activated."""
        logger.info("Conversation cancelled")
        self.reset()

    def on_timeout(self):
        """State timed out."""
        logger.info("Conversation timed out in state %s", self.state.value)
        self.reset()

    @property
    def needs_wake_word(self) -> bool:
        """True if wake word is needed to activate (IDLE state)."""
        return self.state == ConversationState.IDLE

    @property
    def is_active(self) -> bool:
        """True if in an active conversation (not IDLE)."""
        return self.state != ConversationState.IDLE


# ─────────────────────────────────────────────────────────────
# Feedback phrases — spoken during PROCESSING
# ─────────────────────────────────────────────────────────────

FEEDBACK_PHRASES = {
    "search": ["Dejame buscar eso", "Ya lo busco", "Un momento, lo busco"],
    "open_app": ["Ya lo encontre, dejame abrirlo", "Abriendo"],
    "screenshot": ["Dejame ver que hay en pantalla", "Tomando captura"],
    "ocr": ["Leyendo el contenido", "Dejame ver que dice"],
    "close": ["Cerrando", "Listo, lo cierro"],
    "generic": ["Dame un momento", "Trabajando en eso", "Un segundo"],
}


def get_feedback_phrase(action_type: str) -> str:
    """Get a random feedback phrase for an action type."""
    phrases = FEEDBACK_PHRASES.get(action_type, FEEDBACK_PHRASES["generic"])
    return random.choice(phrases)


def classify_intent_type(text: str) -> str:
    """Quick classification of user intent for feedback selection."""
    lower = text.lower()
    if any(w in lower for w in ("busca", "buscar", "search", "clima", "weather")):
        return "search"
    if any(w in lower for w in ("abre", "abrir", "open", "lanza")):
        return "open_app"
    if any(w in lower for w in ("captura", "screenshot", "pantalla")):
        return "screenshot"
    if any(w in lower for w in ("lee", "leer", "que dice", "ocr")):
        return "ocr"
    if any(w in lower for w in ("cierra", "cerrar", "close")):
        return "close"
    return "generic"
