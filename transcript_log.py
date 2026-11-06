"""History of everything the user dictated: raw transcription + AI output.

Each transcription is stored as its own JSON file. On Linux the files live in
$XDG_STATE_HOME/flowclone/transcriptions (default ~/.local/state/...), the XDG
location for history data; elsewhere next to the other user data.

(Not named logging.py: that would shadow the standard library module.)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import APP_DIR

MODE_DICTATION = "dictation"
MODE_COMMAND = "command"

_FORMAT_VERSION = 1


def _log_dir() -> Path:
    if sys.platform.startswith("linux"):
        base = os.environ.get("XDG_STATE_HOME", "").strip()
        # The spec says relative paths are invalid and must be ignored.
        root = (
            Path(base) if base and os.path.isabs(base) else Path.home() / ".local/state"
        )
        return root / "flowclone" / "transcriptions"
    return APP_DIR / "transcriptions"


LOG_DIR = _log_dir()


@dataclass(frozen=True)
class Entry:
    timestamp: datetime  # timezone-aware
    mode: str
    transcription: str
    ai_processed: str | None  # None when no AI processing happened


def record(
    transcription: str, ai_processed: str | None, mode: str = MODE_DICTATION
) -> Path | None:
    """Write one transcription to its own JSON file. Never raises."""
    now = datetime.now().astimezone()
    data = {
        "version": _FORMAT_VERSION,
        "timestamp": now.isoformat(timespec="milliseconds"),
        "mode": mode,
        "transcription": transcription,
        "ai_processed": ai_processed,
    }
    try:
        # Dictations are private: keep the folder and files user-only.
        LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        stem = now.strftime("%Y%m%d-%H%M%S-%f")
        for n in range(100):
            path = LOG_DIR / (f"{stem}.json" if n == 0 else f"{stem}-{n}.json")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return path
    except OSError as e:
        print(f"FlowClone: couldn't save transcription log: {e}", file=sys.stderr)
    return None


def _read(path: Path) -> Entry | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        timestamp = datetime.fromisoformat(data["timestamp"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.astimezone()
        processed = data.get("ai_processed")
        return Entry(
            timestamp=timestamp,
            mode=str(data.get("mode", MODE_DICTATION)),
            transcription=str(data.get("transcription", "")),
            ai_processed=None if processed is None else str(processed),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def load_entries() -> list[Entry]:
    """All readable log entries, newest first."""
    try:
        paths = list(LOG_DIR.glob("*.json"))
    except OSError:
        return []
    entries = [e for e in map(_read, paths) if e is not None]
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries
