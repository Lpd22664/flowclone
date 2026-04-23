"""Config loader. API keys from .env, user preferences from config.json.

Supports multiple LLM providers (OpenAI, Groq). The `provider` preference
determines which API key, base_url, and model names are used at runtime.
Both keys persist — switching providers doesn't wipe the inactive key.

Storage location:
  - When running from source: files live next to the .py files (convenient for dev).
  - When running as a frozen PyInstaller EXE: files live in %APPDATA%\\FlowClone
    (so the installed EXE can write user settings without needing write access
    to Program Files, and the EXE itself stays stateless).
"""
import json
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv, set_key

# Anything key-shaped that might end up in an error message from the API SDK.
# Applied defensively before any error string reaches the UI.
_SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_\-]{10,}|gsk_[A-Za-z0-9_\-]{10,}|Bearer\s+[A-Za-z0-9_.\-]{10,})"
)


def scrub_secrets(text: object) -> str:
    """Redact anything that looks like an API key or bearer token."""
    return _SECRET_PATTERN.sub("<redacted>", str(text))


def _app_dir() -> Path:
    """Return the directory where user data (keys, preferences) lives."""
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA")
        base = (
            Path(appdata) / "FlowClone"
            if appdata
            else Path.home() / ".flowclone"
        )
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(__file__).parent


APP_DIR = _app_dir()
ENV_PATH = APP_DIR / ".env"
CONFIG_PATH = APP_DIR / "config.json"
DICTIONARY_PATH = APP_DIR / "dictionary.txt"

PROVIDER_OPENAI = "openai"
PROVIDER_GROQ = "groq"

DEFAULTS = {
    "provider": PROVIDER_OPENAI,
    "language": "en",
    "push_to_talk_hotkey": "right alt",
    "command_mode_hotkey": "ctrl+shift+space",
    "settings_hotkey": "ctrl+shift+f",
    "ai_cleanup_enabled": True,
    "remove_filler_words": True,
    "min_recording_seconds": 0.5,
    "command_mode_silence_seconds": 1.5,
    "command_mode_silence_threshold": 0.01,
}

# Per-provider model + endpoint config.
PROVIDER_SPEC = {
    PROVIDER_OPENAI: {
        "env_key": "OPENAI_API_KEY",
        "base_url": None,  # SDK default
        "whisper_model": "whisper-1",
        "chat_model": "gpt-4o-mini",
        "display": "OpenAI",
    },
    PROVIDER_GROQ: {
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "whisper_model": "whisper-large-v3-turbo",
        "chat_model": "llama-3.3-70b-versatile",
        "display": "Groq (free)",
    },
}


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        load_dotenv(ENV_PATH)
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                for k, v in user_cfg.items():
                    if k in DEFAULTS:
                        self._data[k] = v
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=4)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def all(self):
        return dict(self._data)

    # --- Provider selection --------------------------------------------

    @property
    def provider(self) -> str:
        p = self._data.get("provider", PROVIDER_OPENAI)
        return p if p in PROVIDER_SPEC else PROVIDER_OPENAI

    def _spec(self) -> dict:
        return PROVIDER_SPEC[self.provider]

    @property
    def provider_api_key(self) -> str:
        """API key for the *currently selected* provider."""
        return os.environ.get(self._spec()["env_key"], "").strip()

    @property
    def provider_base_url(self) -> str | None:
        return self._spec()["base_url"]

    @property
    def whisper_model(self) -> str:
        return self._spec()["whisper_model"]

    @property
    def chat_model(self) -> str:
        return self._spec()["chat_model"]

    @property
    def provider_display_name(self) -> str:
        return self._spec()["display"]

    def api_key_for(self, provider: str) -> str:
        """Read the stored key for a specific provider (regardless of which is active)."""
        spec = PROVIDER_SPEC.get(provider)
        if spec is None:
            return ""
        return os.environ.get(spec["env_key"], "").strip()

    def set_api_key_for(self, provider: str, key: str):
        """Persist a key to .env for a specific provider."""
        spec = PROVIDER_SPEC.get(provider)
        if spec is None:
            return
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        set_key(str(ENV_PATH), spec["env_key"], key)
        os.environ[spec["env_key"]] = key

    # --- Legacy aliases (keep existing callsites working) -------------

    @property
    def api_key(self) -> str:
        """Deprecated: kept for backward compatibility. Prefer provider_api_key."""
        return self.provider_api_key

    def set_api_key(self, key: str):
        """Deprecated alias; writes to the OpenAI slot."""
        self.set_api_key_for(PROVIDER_OPENAI, key)

    # --- Dictionary ----------------------------------------------------

    def dictionary_words(self) -> list[str]:
        if not DICTIONARY_PATH.exists():
            return []
        with open(DICTIONARY_PATH, "r", encoding="utf-8") as f:
            out = []
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                out.append(s)
            return out

    def save_dictionary(self, text: str):
        with open(DICTIONARY_PATH, "w", encoding="utf-8") as f:
            f.write(text)

    def whisper_prompt(self) -> str | None:
        words = self.dictionary_words()
        if not words:
            return None
        return "The following terms may appear: " + ", ".join(words) + "."


config = Config()
