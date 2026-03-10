"""Telegram bridge — bot interaction with security + goal routing.

Implements BridgeBase for Telegram. Receives text/voice messages,
routes them as goals, sends results back as messages.

/ Bridge de Telegram — interaccion por bot con seguridad.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from typing import Optional

from marlow.bridges.base import BridgeBase

logger = logging.getLogger("marlow.bridges.telegram")


class TelegramBridge(BridgeBase):
    """Telegram bot bridge: messages in -> goals -> results out."""

    def __init__(self):
        self._app = None
        self._bot = None
        self._security = None
        self._goal_callback = None
        self._active_chat_id: Optional[int] = None
        self._whisper_model = None

    @property
    def channel_name(self) -> str:
        return "telegram"

    # ── BridgeBase implementation ──

    async def send_text(self, text: str, **kwargs):
        """Send text message to the active chat."""
        chat_id = kwargs.get("chat_id", self._active_chat_id)
        if not chat_id or not self._bot:
            return
        try:
            # Split long messages (Telegram limit 4096 chars)
            for chunk in _split_message(text):
                await self._bot.send_message(chat_id, chunk)
        except Exception as e:
            logger.error("Failed to send message: %s", e)

    async def send_file(self, file_path: str, caption: str = "", **kwargs):
        chat_id = kwargs.get("chat_id", self._active_chat_id)
        if not chat_id or not self._bot:
            return
        try:
            with open(file_path, "rb") as f:
                await self._bot.send_document(chat_id, f, caption=caption)
        except Exception as e:
            logger.error("Failed to send file: %s", e)

    async def send_photo(self, image_bytes: bytes, caption: str = "", **kwargs):
        chat_id = kwargs.get("chat_id", self._active_chat_id)
        if not chat_id or not self._bot:
            return
        try:
            await self._bot.send_photo(chat_id, image_bytes, caption=caption)
        except Exception as e:
            logger.error("Failed to send photo: %s", e)

    async def notify(self, message: str, level: str = "info", **kwargs):
        prefix = {"error": "ERROR", "warning": "AVISO", "info": ""}.get(level, "")
        text = f"{prefix}: {message}" if prefix else message
        await self.send_text(text, **kwargs)

    async def ask(self, question: str, options: Optional[list[str]] = None, **kwargs) -> str:
        """Ask via inline keyboard or text prompt."""
        chat_id = kwargs.get("chat_id", self._active_chat_id)
        if not chat_id or not self._bot:
            return ""

        if options:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [InlineKeyboardButton(opt, callback_data=f"opt_{i}")]
                for i, opt in enumerate(options)
            ]
            keyboard.append([InlineKeyboardButton("Cancelar", callback_data="cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self._bot.send_message(chat_id, question, reply_markup=reply_markup)
        else:
            await self._bot.send_message(chat_id, question)

        return ""  # Response comes via callback handler

    # ── Setup and lifecycle ──

    async def start(self, goal_callback):
        """Start the Telegram bot with polling.

        goal_callback: async callable(text, channel) -> dict
        """
        from telegram.ext import (
            Application,
            CommandHandler,
            MessageHandler,
            CallbackQueryHandler,
            filters,
        )
        from marlow.bridges.telegram.security import TelegramSecurity
        from marlow.core.settings import get_settings

        settings = get_settings()
        token = settings.secrets.telegram_bot_token
        if not token:
            token = os.environ.get("MARLOW_TELEGRAM_TOKEN", "")
        if not token:
            logger.error("No Telegram bot token configured")
            return

        self._goal_callback = goal_callback
        self._security = TelegramSecurity(
            authorized_ids=settings.telegram.authorized_ids,
            require_passphrase=settings.telegram.require_passphrase,
        )

        self._app = Application.builder().token(token).build()
        self._bot = self._app.bot

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("screenshot", self._cmd_screenshot))
        self._app.add_handler(CommandHandler("menu", self._cmd_menu))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self._app.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot started polling")

    async def stop(self):
        """Stop the Telegram bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")

    # ── Handlers ──

    async def _cmd_start(self, update, context):
        """Handle /start command."""
        chat_id = update.effective_chat.id
        user = update.effective_user

        if self._security.needs_passphrase(chat_id):
            await update.message.reply_text(
                "Hola! Para usar Marlow, envia la clave de acceso."
            )
            return

        if not self._security.is_authorized(chat_id):
            # Auto-authorize on first /start if no whitelist set
            self._security.authorize(chat_id)
            # Save to settings
            try:
                from marlow.core.settings import update_setting
                ids = list(self._security.authorized_ids)
                update_setting("telegram", "authorized_ids", ids)
            except Exception:
                pass

        name = user.first_name if user else "amigo"
        await update.message.reply_text(
            f"Hola {name}! Soy Marlow, tu asistente de escritorio.\n\n"
            f"Puedes enviarme comandos en texto o notas de voz.\n"
            f"Usa /menu para ver opciones rapidas."
        )

    async def _cmd_status(self, update, context):
        """Handle /status command."""
        if not self._check_auth(update):
            return

        import urllib.request
        try:
            with urllib.request.urlopen("http://localhost:8420/status", timeout=5) as r:
                data = json.loads(r.read())
            state = data.get("state", "?")
            uptime = data.get("uptime_s", 0)
            tools = data.get("tools_registered", 0)
            goal = data.get("current_goal", "ninguno")

            await update.message.reply_text(
                f"Estado: {state}\n"
                f"Uptime: {uptime:.0f}s\n"
                f"Tools: {tools}\n"
                f"Goal actual: {goal or 'ninguno'}"
            )
        except Exception as e:
            logger.error("Status check failed: %s", e)
            await update.message.reply_text(
                "El daemon no esta disponible en este momento."
            )

    async def _cmd_screenshot(self, update, context):
        """Handle /screenshot command."""
        if not self._check_auth(update):
            return
        chat_id = update.effective_chat.id
        self._active_chat_id = chat_id
        await self._take_and_send_screenshot(chat_id, context)

    async def _take_and_send_screenshot(self, chat_id, context):
        """Take a screenshot via grim and send it as a photo."""
        tmp_path = os.path.join(tempfile.gettempdir(), "marlow_tg_screenshot.png")
        try:
            result = subprocess.run(
                ["grim", tmp_path], capture_output=True, timeout=10,
            )
            if result.returncode == 0 and os.path.exists(tmp_path):
                with open(tmp_path, "rb") as f:
                    await context.bot.send_photo(
                        chat_id, f, caption="Captura de pantalla",
                    )
            else:
                await context.bot.send_message(
                    chat_id, "No pude tomar la captura.",
                )
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            await context.bot.send_message(
                chat_id, "No pude tomar la captura.",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    async def _cmd_menu(self, update, context):
        """Handle /menu — show inline keyboard with main actions."""
        if not self._check_auth(update):
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("Estado del sistema", callback_data="menu_status")],
            [InlineKeyboardButton("Captura de pantalla", callback_data="menu_screenshot")],
            [InlineKeyboardButton("Lista de ventanas", callback_data="menu_windows")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Que necesitas?", reply_markup=reply_markup)

    async def _handle_message(self, update, context):
        """Handle text messages — route as goals."""
        if not self._check_auth(update):
            return
        if not self._check_rate(update):
            return

        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        self._active_chat_id = chat_id

        if not text:
            return

        # Check passphrase for pending auth
        if self._security.needs_passphrase(chat_id):
            if self._security.check_passphrase(chat_id, text):
                await update.message.reply_text("Autenticado! Ya puedes usar Marlow.")
                try:
                    from marlow.core.settings import update_setting
                    ids = list(self._security.authorized_ids)
                    update_setting("telegram", "authorized_ids", ids)
                except Exception:
                    pass
            else:
                await update.message.reply_text("Clave incorrecta.")
            return

        # Send as goal
        msg = await update.message.reply_text("Procesando...")

        try:
            result = await self._goal_callback(text, "telegram")
            response = (
                result.get("response")
                or result.get("result_summary")
                or ("Listo." if result.get("success") else "No pude completar la tarea.")
            )
            await context.bot.edit_message_text(
                response, chat_id, msg.message_id,
            )
        except Exception as e:
            logger.error("Telegram message handler failed: %s", e)
            await context.bot.edit_message_text(
                "No pude procesar tu solicitud. Intenta de nuevo.",
                chat_id, msg.message_id,
            )

    async def _handle_voice(self, update, context):
        """Handle voice notes — download, transcribe, process."""
        if not self._check_auth(update):
            return
        if not self._check_rate(update):
            return

        chat_id = update.effective_chat.id
        self._active_chat_id = chat_id
        await update.message.reply_text("Escuchando nota de voz...")

        tmp_path = None
        wav_path = None
        try:
            voice = update.message.voice or update.message.audio
            file = await context.bot.get_file(voice.file_id)

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await file.download_to_drive(tmp.name)
                tmp_path = tmp.name

            wav_path = tmp_path.replace(".ogg", ".wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True, timeout=30,
            )

            if not os.path.exists(wav_path):
                await update.message.reply_text("No pude procesar el audio.")
                return

            model = self._get_whisper_model()
            segments, _ = model.transcribe(wav_path, language="es", beam_size=5)
            text = " ".join(s.text for s in segments).strip()

            if not text or len(text) < 3:
                await update.message.reply_text("No pude entender la nota de voz.")
                return

            await update.message.reply_text(f'Escuche: "{text}"')
            result = await self._goal_callback(text, "telegram")
            response = (
                result.get("response")
                or result.get("result_summary")
                or ("Listo." if result.get("success") else "No pude completar la tarea.")
            )
            await update.message.reply_text(response)

        except Exception as e:
            logger.error("Voice note error: %s", e)
            await update.message.reply_text(
                "No pude procesar la nota de voz. Intenta de nuevo.",
            )
        finally:
            for f in [tmp_path, wav_path]:
                if f:
                    try:
                        os.unlink(f)
                    except FileNotFoundError:
                        pass

    async def _handle_callback(self, update, context):
        """Handle inline keyboard callbacks."""
        query = update.callback_query
        await query.answer()
        data = query.data

        chat_id = query.message.chat_id
        self._active_chat_id = chat_id

        if data == "menu_status":
            import urllib.request
            try:
                with urllib.request.urlopen(
                    "http://localhost:8420/status", timeout=5,
                ) as r:
                    d = json.loads(r.read())
                await query.message.reply_text(
                    f"Estado: {d.get('state', '?')}"  # noqa
                    f"\nUptime: {d.get('uptime_s', 0):.0f}s"
                    f"\nTools: {d.get('tools_registered', 0)}"
                )
            except Exception:
                await query.message.reply_text("Daemon no disponible.")
        elif data == "menu_screenshot":
            await self._take_and_send_screenshot(chat_id, context)
        elif data == "menu_windows":
            result = await self._goal_callback("lista de ventanas abiertas", "telegram")
            response = (
                result.get("response")
                or result.get("result_summary")
                or "No pude obtener la lista."
            )
            await query.message.reply_text(response)
        elif data == "cancel":
            await query.message.reply_text("Cancelado")
        elif data.startswith("opt_"):
            idx = int(data.split("_")[1])
            await query.message.reply_text(f"Seleccionaste opcion {idx + 1}")

    # ── Helpers ──

    def _get_whisper_model(self):
        """Lazy-load whisper model (reused across voice notes)."""
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading whisper model for Telegram voice notes...")
            self._whisper_model = WhisperModel(
                "base", device="cpu", compute_type="int8",
            )
        return self._whisper_model

    def _check_auth(self, update) -> bool:
        """Check if message sender is authorized."""
        chat_id = update.effective_chat.id
        if not self._security.is_authorized(chat_id):
            return False
        return True

    def _check_rate(self, update) -> bool:
        """Check rate limit for sender."""
        chat_id = update.effective_chat.id
        if not self._security.check_rate_limit(chat_id):
            asyncio.create_task(
                update.message.reply_text("Demasiados mensajes. Espera un momento.")
            )
            return False
        return True


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split text into chunks that fit Telegram message limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find last newline before limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
