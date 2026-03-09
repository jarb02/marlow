"""OCR → LLM summary pipeline with intent context.

When a goal involves reading screen content, the raw OCR text is
summarized by the LLM with the user's original intent as context.
The summary is natural Spanish suitable for TTS output.

/ Pipeline OCR -> LLM con contexto de intent del usuario.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("marlow.kernel.ocr_summary")

# ─────────────────────────────────────────────────────────────
# Prompt template
# ─────────────────────────────────────────────────────────────

OCR_SUMMARY_PROMPT = """CONTEXTO: El usuario esta interactuando por {channel} con su desktop Linux.
INTENT DEL USUARIO: "{user_intent}"
VENTANA ACTIVA: "{window_title}" (app: {app_id})

CONTENIDO OCR DE PANTALLA:
---
{ocr_text}
---

INSTRUCCION: Resume SOLO la informacion relevante al intent del usuario.
Maximo 2-3 oraciones naturales en espanol. Si el intent es una pregunta, responde directamente.
Ignora elementos de UI irrelevantes (menus, barras de herramientas, decoraciones de ventana).
La respuesta sera hablada por TTS, asi que debe sonar natural al oido.
No uses markdown, asteriscos, ni formatos especiales — solo texto plano."""


# ─────────────────────────────────────────────────────────────
# Summarizer
# ─────────────────────────────────────────────────────────────

async def summarize_ocr(
    ocr_text: str,
    user_intent: str,
    window_title: str = "",
    app_id: str = "",
    channel: str = "voice",
    max_tokens: int = 300,
) -> str:
    """Summarize OCR text using LLM with user intent as context.

    Returns a natural Spanish summary suitable for TTS, or the raw
    OCR text truncated if LLM is unavailable.
    """
    if not ocr_text or not ocr_text.strip():
        return "No pude leer contenido de la pantalla."

    # Try LLM summary
    try:
        summary = await _llm_summarize(
            ocr_text, user_intent, window_title, app_id, channel, max_tokens,
        )
        if summary:
            return summary
    except Exception as e:
        logger.warning("LLM summary failed: %s", e)

    # Fallback: truncate raw OCR text
    return _fallback_truncate(ocr_text, user_intent)


async def _llm_summarize(
    ocr_text: str,
    user_intent: str,
    window_title: str,
    app_id: str,
    channel: str,
    max_tokens: int,
) -> Optional[str]:
    """Call LLM provider with the OCR summary prompt."""
    try:
        from marlow.kernel.cognition.providers import (
            AnthropicProvider,
            ProviderConfig,
        )
        from marlow.core.settings import get_settings
    except ImportError:
        return None

    settings = get_settings()
    api_key = settings.secrets.anthropic_api_key
    if not api_key:
        logger.debug("No API key, skipping LLM summary")
        return None

    # Truncate OCR to avoid token waste (max ~2000 chars)
    truncated_ocr = ocr_text[:2000]

    prompt = OCR_SUMMARY_PROMPT.format(
        channel=channel,
        user_intent=user_intent,
        window_title=window_title or "desconocida",
        app_id=app_id or "desconocido",
        ocr_text=truncated_ocr,
    )

    config = ProviderConfig(
        api_key=api_key,
        model="claude-sonnet-4-20250514",
        timeout=15.0,
        max_retries=1,
    )
    provider = AnthropicProvider(config)

    result = await provider.generate(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.3,
    )

    if result and len(result.strip()) > 10:
        return result.strip()

    return None


def _fallback_truncate(ocr_text: str, user_intent: str) -> str:
    """Fallback: extract most relevant lines from OCR text."""
    lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]
    if not lines:
        return "No encontre informacion relevante."

    # Simple keyword matching from intent
    intent_words = set(user_intent.lower().split())
    scored = []
    for line in lines:
        line_words = set(line.lower().split())
        overlap = len(intent_words & line_words)
        scored.append((overlap, line))

    # Sort by relevance, take top 3 lines
    scored.sort(key=lambda x: x[0], reverse=True)
    relevant = [line for _, line in scored[:3] if line]

    if relevant:
        return " ".join(relevant)[:300]

    # Just return first few lines
    return " ".join(lines[:3])[:300]
