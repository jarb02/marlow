"""Marlow OS settings — TOML-based configuration for Phase 9.5+.

Manages user preferences, voice settings, TTS engine, sidebar, Telegram,
and secrets. Reads from ~/.config/marlow/config.toml and secrets.toml.

Coexists with core/config.py (JSON-based security/automation config).
This module handles user-facing settings; config.py handles internals.

/ Configuracion TOML para Phase 9.5 — voz, TTS, sidebar, Telegram.
"""

from __future__ import annotations

import logging
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.core.settings")

# XDG-compliant config directory
SETTINGS_DIR = Path.home() / ".config" / "marlow"
CONFIG_PATH = SETTINGS_DIR / "config.toml"
SECRETS_PATH = SETTINGS_DIR / "secrets.toml"
VOICE_CLIPS_DIR = SETTINGS_DIR / "voice_clips"


# ─────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class UserSettings:
    name: str = ""
    language: str = "es"

@dataclass
class VoiceSettings:
    enabled: bool = True
    engine: str = "auto"  # auto | gemini-live | local
    wake_word: bool = True
    wake_word_model: str = "marlow"
    push_to_talk_key: str = "Super+V"
    silence_timeout: float = 1.5
    vad_backend: str = "auto"  # auto | webrtc | silero | energy

@dataclass
class TTSSettings:
    engine: str = "auto"  # auto | kokoro | edge-tts | piper | espeak
    kokoro_voice: str = "ef_dora"
    edge_tts_voice: str = "es-MX-JorgeNeural"
    piper_voice: str = "es_MX-medium"

@dataclass
class WhisperSettings:
    model: str = "auto"  # auto | tiny | base | small | medium | large-v3
    language: str = "es"
    compute_type: str = "auto"  # auto | int8 | float16

@dataclass
class SidebarSettings:
    enabled: bool = True
    width: int = 380
    position: str = "right"

@dataclass
class TelegramNotifications:
    goal_completed: bool = True
    goal_failed: bool = True
    system_alerts: bool = True

@dataclass
class TelegramFiles:
    max_send_size_mb: int = 50
    download_directory: str = "~/Downloads/telegram"

@dataclass
class TelegramSettings:
    enabled: bool = False
    token_env: str = "MARLOW_TELEGRAM_TOKEN"
    authorized_ids: list[int] = field(default_factory=list)
    require_passphrase: bool = False
    language: str = "es"
    notifications: TelegramNotifications = field(default_factory=TelegramNotifications)
    files: TelegramFiles = field(default_factory=TelegramFiles)

@dataclass
class GeminiSettings:
    api_key_env: str = "GEMINI_API_KEY"
    model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    voice: str = ""  # empty = default, or: Puck, Kore, Charon, Aoede, etc.
    language: str = "es"

@dataclass
class PrivacySettings:
    ambient_awareness: bool = False
    excluded_windows: list[str] = field(default_factory=list)
    event_buffer_minutes: int = 5

@dataclass
class Secrets:
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    telegram_bot_token: str = ""


# ─────────────────────────────────────────────────────────────
# Main settings class
# ─────────────────────────────────────────────────────────────

@dataclass
class MarlowSettings:
    """All user-facing settings for Marlow OS."""
    user: UserSettings = field(default_factory=UserSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    whisper: WhisperSettings = field(default_factory=WhisperSettings)
    sidebar: SidebarSettings = field(default_factory=SidebarSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    secrets: Secrets = field(default_factory=Secrets)

    @property
    def is_onboarded(self) -> bool:
        """True if the user has completed onboarding (has a name set)."""
        return bool(self.user.name)

    @property
    def has_llm(self) -> bool:
        """True if an LLM API key is configured."""
        return bool(self.secrets.anthropic_api_key)

    @property
    def has_gemini(self) -> bool:
        """True if a Gemini API key is configured."""
        return bool(self.secrets.gemini_api_key)


# ─────────────────────────────────────────────────────────────
# TOML serialization (write)
# ─────────────────────────────────────────────────────────────

def _serialize_toml(data: dict) -> str:
    """Minimal TOML serializer for nested dicts (tomllib is read-only)."""
    lines: list[str] = []

    # Separate simple values from tables
    simple = {}
    tables = {}
    for k, v in data.items():
        if isinstance(v, dict):
            tables[k] = v
        else:
            simple[k] = v

    # Write simple values first
    for k, v in simple.items():
        lines.append(f"{k} = {_toml_value(v)}")

    # Write tables
    for k, v in tables.items():
        lines.append("")
        lines.append(f"[{k}]")
        for sk, sv in v.items():
            if isinstance(sv, dict):
                lines.append("")
                lines.append(f"[{k}.{sk}]")
                for ssk, ssv in sv.items():
                    lines.append(f"{ssk} = {_toml_value(ssv)}")
            else:
                lines.append(f"{sk} = {_toml_value(sv)}")

    return "\n".join(lines) + "\n"


def _toml_value(v) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        if not v:
            return "[]"
        items = ", ".join(_toml_value(i) for i in v)
        return f"[{items}]"
    return f'"{v}"'


# ─────────────────────────────────────────────────────────────
# Load / Save
# ─────────────────────────────────────────────────────────────

def _ensure_dirs():
    """Create config directories if needed."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_CLIPS_DIR.mkdir(parents=True, exist_ok=True)


def _load_toml(path: Path) -> dict:
    """Load a TOML file, return empty dict on failure."""
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return {}


def _populate_dataclass(dc_class, data: dict):
    """Create a dataclass instance from a dict, ignoring unknown keys."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(dc_class)}
    filtered = {k: v for k, v in data.items() if k in field_names and not isinstance(v, dict)}
    return dc_class(**filtered)


def load_settings() -> MarlowSettings:
    """Load settings from config.toml + secrets.toml + environment.

    Priority: environment vars > secrets.toml > config.toml > defaults.
    """
    _ensure_dirs()
    config = _load_toml(CONFIG_PATH)
    secrets_data = _load_toml(SECRETS_PATH)

    settings = MarlowSettings()

    # Populate each section from TOML
    if "user" in config:
        settings.user = _populate_dataclass(UserSettings, config["user"])
    if "voice" in config:
        settings.voice = _populate_dataclass(VoiceSettings, config["voice"])
    if "tts" in config:
        settings.tts = _populate_dataclass(TTSSettings, config["tts"])
    if "whisper" in config:
        settings.whisper = _populate_dataclass(WhisperSettings, config["whisper"])
    if "sidebar" in config:
        settings.sidebar = _populate_dataclass(SidebarSettings, config["sidebar"])
    if "telegram" in config:
        tg = config["telegram"]
        settings.telegram = _populate_dataclass(TelegramSettings, tg)
        if "notifications" in tg:
            settings.telegram.notifications = _populate_dataclass(
                TelegramNotifications, tg["notifications"],
            )
        if "files" in tg:
            settings.telegram.files = _populate_dataclass(
                TelegramFiles, tg["files"],
            )
    if "gemini" in config:
        settings.gemini = _populate_dataclass(GeminiSettings, config["gemini"])
    if "privacy" in config:
        settings.privacy = _populate_dataclass(PrivacySettings, config["privacy"])

    # Load secrets (TOML file)
    anthropic = secrets_data.get("anthropic", {})
    gemini_sec = secrets_data.get("gemini", {})
    telegram_sec = secrets_data.get("telegram", {})
    settings.secrets = Secrets(
        anthropic_api_key=anthropic.get("api_key", ""),
        gemini_api_key=gemini_sec.get("api_key", ""),
        telegram_bot_token=telegram_sec.get("bot_token", ""),
    )

    # Environment variables override secrets.toml
    env_anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_anthropic:
        settings.secrets.anthropic_api_key = env_anthropic
    env_gemini = os.environ.get("GEMINI_API_KEY", "")
    if not env_gemini:
        env_gemini = os.environ.get("MARLOW_GEMINI_API_KEY", "")
    if env_gemini:
        settings.secrets.gemini_api_key = env_gemini
    env_telegram = os.environ.get("MARLOW_TELEGRAM_TOKEN", "")
    if env_telegram:
        settings.secrets.telegram_bot_token = env_telegram

    return settings


def save_settings(settings: MarlowSettings):
    """Save settings to config.toml (no secrets) and secrets.toml."""
    _ensure_dirs()

    # Build config dict (everything except secrets)
    import dataclasses
    config = {}
    for f in dataclasses.fields(settings):
        if f.name == "secrets":
            continue
        val = getattr(settings, f.name)
        if dataclasses.is_dataclass(val):
            section = {}
            for sf in dataclasses.fields(val):
                sv = getattr(val, sf.name)
                if dataclasses.is_dataclass(sv):
                    sub = {}
                    for ssf in dataclasses.fields(sv):
                        sub[ssf.name] = getattr(sv, ssf.name)
                    section[sf.name] = sub
                else:
                    section[sf.name] = sv
            config[f.name] = section

    # Write config.toml
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(_serialize_toml(config))
    logger.info("Saved config to %s", CONFIG_PATH)

    # Write secrets.toml (only if there are secrets to save)
    if settings.secrets.anthropic_api_key or settings.secrets.gemini_api_key or settings.secrets.telegram_bot_token:
        secrets_dict = {}
        if settings.secrets.anthropic_api_key:
            secrets_dict["anthropic"] = {"api_key": settings.secrets.anthropic_api_key}
        if settings.secrets.gemini_api_key:
            secrets_dict["gemini"] = {"api_key": settings.secrets.gemini_api_key}
        if settings.secrets.telegram_bot_token:
            secrets_dict["telegram"] = {"bot_token": settings.secrets.telegram_bot_token}

        with open(SECRETS_PATH, "w", encoding="utf-8") as f:
            f.write(_serialize_toml(secrets_dict))

        # chmod 600 — owner read/write only
        os.chmod(SECRETS_PATH, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("Saved secrets to %s (chmod 600)", SECRETS_PATH)


def save_secret(key: str, value: str):
    """Save a single secret without loading full settings.

    key: 'anthropic_api_key' or 'telegram_bot_token'
    """
    _ensure_dirs()
    existing = _load_toml(SECRETS_PATH)

    if key == "anthropic_api_key":
        existing.setdefault("anthropic", {})["api_key"] = value
    elif key == "gemini_api_key":
        existing.setdefault("gemini", {})["api_key"] = value
    elif key == "telegram_bot_token":
        existing.setdefault("telegram", {})["bot_token"] = value
    else:
        logger.warning("Unknown secret key: %s", key)
        return

    with open(SECRETS_PATH, "w", encoding="utf-8") as f:
        f.write(_serialize_toml(existing))
    os.chmod(SECRETS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    logger.info("Saved secret '%s' to %s", key, SECRETS_PATH)


def update_setting(section: str, key: str, value):
    """Update a single setting in config.toml without overwriting everything."""
    _ensure_dirs()
    existing = _load_toml(CONFIG_PATH)
    existing.setdefault(section, {})[key] = value

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(_serialize_toml(existing))
    logger.info("Updated [%s].%s in %s", section, key, CONFIG_PATH)


# ─────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────

_settings: MarlowSettings | None = None


def get_settings() -> MarlowSettings:
    """Get or load the singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reload_settings() -> MarlowSettings:
    """Force reload settings from disk."""
    global _settings
    _settings = load_settings()
    return _settings
