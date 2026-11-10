"""Whisper API transcription."""

import io
import time

from openai import OpenAI, APIConnectionError, APITimeoutError

from config import config, scrub_secrets
import whispercpp


class TranscriptionError(RuntimeError):
    pass


def _transcribe_local(wav_bytes: bytes) -> str:
    model = config.get("whisper_model", "base")
    try:
        whispercpp.server.start(model)  # no-op if already running this model
        return whispercpp.server.transcribe(
            wav_bytes, config.get("language", "en"), config.whisper_prompt()
        )
    except whispercpp.WhisperServerError as e:
        raise TranscriptionError(str(e)) from e


def _client() -> OpenAI:
    api_key = config.provider_api_key
    if not api_key:
        raise TranscriptionError(f"No API key for {config.provider_display_name}")
    kwargs = {"api_key": api_key, "timeout": 30.0}
    if config.provider_base_url:
        kwargs["base_url"] = config.provider_base_url
    return OpenAI(**kwargs)


def transcribe(wav_bytes: bytes) -> str:
    """Send WAV bytes to Whisper, return raw transcript. Retries once on network errors."""
    if not wav_bytes:
        return ""
    if config.is_local:
        return _transcribe_local(wav_bytes)

    client = _client()
    language = config.get("language", "en")
    prompt = config.whisper_prompt()
    model = config.whisper_model

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            buf = io.BytesIO(wav_bytes)
            buf.name = "audio.wav"
            kwargs = {
                "model": model,
                "file": buf,
                "response_format": "text",
            }
            if language and language != "auto":
                kwargs["language"] = language
            if prompt:
                kwargs["prompt"] = prompt

            result = client.audio.transcriptions.create(**kwargs)
            text = result if isinstance(result, str) else getattr(result, "text", "")
            return (text or "").strip()
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise TranscriptionError(f"Network error: {scrub_secrets(e)}") from e
        except Exception as e:
            raise TranscriptionError(scrub_secrets(e)) from e

    raise TranscriptionError(scrub_secrets(last_err) if last_err else "Unknown error")
