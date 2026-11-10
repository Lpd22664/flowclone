"""llama.cpp GGUF model catalogue, downloader and llama-server supervisor.

Models are stored in <app dir>/llama-models and verified against the SHA-256
published by Hugging Face. `server` supervises a local llama-server process
(from <app dir>/bin, see scripts/build_llama_cpp.py) that serves an
OpenAI-compatible API at `server.url + "/v1"`.

CLI usage:
    python llamacpp.py <model> [models_path]   # download a model
    python llamacpp.py                         # list available models
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import APP_DIR
from whispercpp import (
    DownloadCancelled,
    DownloadError,
    ProgressCallback,
    download_file,
    format_size,
)

MODELS_DIR = APP_DIR / "llama-models"
BIN_DIR = APP_DIR / "bin"
SERVER_LOG = APP_DIR / "llama-server.log"


@dataclass(frozen=True)
class ModelInfo:
    name: str
    repo: str  # Hugging Face repo, e.g. "owner/name"
    filename: str  # GGUF file in that repo
    size: int  # bytes
    sha256: str
    description: str = ""

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/main/{self.filename}"


# Size and SHA-256 come from the Hugging Face file listing, e.g.
#   https://huggingface.co/api/models/<repo>/tree/main  ("size", "lfs.oid")
_MODEL_LIST: tuple[ModelInfo, ...] = (
    ModelInfo(
        name="smollm2-1.7b-instruct-q4_k_m",
        repo="HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF",
        filename="smollm2-1.7b-instruct-q4_k_m.gguf",
        size=1055609536,
        sha256="decd2598bc2c8ed08c19adc3c8fdd461ee19ed5708679d1c54ef54a5a30d4f33",
        description="SmolLM2 1.7B Instruct, Q4_K_M",
    ),
)

MODELS: dict[str, ModelInfo] = {m.name: m for m in _MODEL_LIST}


def model_path(name: str, models_dir: Optional[Path] = None) -> Path:
    return Path(models_dir or MODELS_DIR) / MODELS[name].filename


def is_downloaded(name: str, models_dir: Optional[Path] = None) -> bool:
    path = model_path(name, models_dir)
    return path.is_file() and path.stat().st_size == MODELS[name].size


def download_model(
    name: str,
    models_dir: Optional[Path] = None,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """Download model `name` into models_dir (default: llama-models).

    Blocking. Resumes partial downloads, retries on network errors, and
    verifies the SHA-256. `progress(done, total)` is called from this thread;
    setting `cancel` aborts with DownloadCancelled (the partial file is kept
    so a later call resumes).
    """
    if name not in MODELS:
        raise DownloadError(f"Invalid model: {name}")
    info = MODELS[name]
    dest = model_path(name, models_dir)
    if is_downloaded(name, models_dir):
        return dest
    return download_file(
        info.url, dest, info.size, info.sha256, name, progress=progress, cancel=cancel
    )


# --- llama-server supervisor ---------------------------------------------


class LlamaServerError(RuntimeError):
    pass


def server_binary() -> Path:
    return BIN_DIR / ("llama-server.exe" if sys.platform == "win32" else "llama-server")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Never route localhost requests through an HTTP proxy from the environment.
_local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_MAX_RESTARTS = 3  # within _RESTART_WINDOW seconds before giving up
_RESTART_WINDOW = 60.0
_RESTART_DELAY = 1.0


class LlamaServer:
    """Runs llama-server in the background and restarts it if it crashes."""

    def __init__(self, host: str = "127.0.0.1"):
        self._host = host
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._model: Optional[str] = None
        self._port: Optional[int] = None
        self._generation = 0  # bumped on every start/stop; stale supervisors exit
        self._ready = threading.Event()
        self.last_error = ""

    @property
    def model(self) -> Optional[str]:
        return self._model

    @property
    def url(self) -> Optional[str]:
        """Server root; the OpenAI-compatible API is at url + "/v1"."""
        with self._lock:
            return f"http://{self._host}:{self._port}" if self._port else None

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def is_ready(self) -> bool:
        return self._ready.is_set() and self.is_running()

    def start(self, model: str) -> None:
        """Start the server with `model`; restarts it if another model is loaded."""
        binary = server_binary()
        if not binary.is_file():
            raise LlamaServerError(f"llama-server not found in {BIN_DIR}")
        if model not in MODELS or not is_downloaded(model):
            raise LlamaServerError(f"Model {model} is not downloaded")
        with self._lock:
            if self._model == model and self._proc is not None:
                return  # already running (or being restarted by the supervisor)
            self._stop_locked()
            self._model = model
            self.last_error = ""
            self._generation += 1
            gen = self._generation
            self._spawn_locked()
        threading.Thread(
            target=self._supervise,
            args=(gen,),
            name="llama-server-supervisor",
            daemon=True,
        ).start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._generation += 1
        self._ready.clear()
        proc, self._proc = self._proc, None
        self._model = None
        self._port = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _spawn_locked(self) -> None:
        self._ready.clear()
        self._port = _free_port()
        # GPU offload and context size are left to llama-server's defaults
        # (all layers on the GPU, fitted to the available memory).
        cmd = [
            str(server_binary()),
            "-m",
            str(model_path(self._model)),
            "--alias",
            self._model,
            "--host",
            self._host,
            "--port",
            str(self._port),
        ]
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        with open(SERVER_LOG, "wb") as log:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(BIN_DIR),
                **kwargs,
            )

    def _poll_ready(self, gen: int, proc: subprocess.Popen) -> None:
        # /health answers 503 while the model is loading and 200 once ready.
        while proc.poll() is None:
            with self._lock:
                if gen != self._generation:
                    return
                url = self.url
            try:
                with _local_opener.open(f"{url}/health", timeout=2) as resp:
                    if resp.status == 200:
                        self._ready.set()
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.2)

    def _supervise(self, gen: int) -> None:
        crashes: list[float] = []
        while True:
            with self._lock:
                if gen != self._generation or self._proc is None:
                    return
                proc = self._proc
            self._poll_ready(gen, proc)
            code = proc.wait()
            with self._lock:
                if gen != self._generation:
                    return  # stopped or replaced on purpose
                self._ready.clear()
                now = time.monotonic()
                crashes = [t for t in crashes if now - t < _RESTART_WINDOW] + [now]
                if len(crashes) > _MAX_RESTARTS:
                    self.last_error = f"llama-server keeps exiting (code {code}); see {SERVER_LOG.name}"
                    self._proc = None
                    self._port = None
                    return
                self.last_error = f"llama-server exited (code {code}); restarting"
            time.sleep(_RESTART_DELAY * len(crashes))
            with self._lock:
                if gen != self._generation:
                    return
                self._spawn_locked()

    def wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready.wait(timeout=0.2):
                return True
            with self._lock:
                if self._proc is None:
                    return False
        return self._ready.is_set()


server = LlamaServer()
atexit.register(server.stop)


# --- CLI ------------------------------------------------------------------


def _list_models(models_dir: Path = MODELS_DIR) -> None:
    print("\nAvailable models:")
    for info in MODELS.values():
        mark = "*" if is_downloaded(info.name, models_dir) else " "
        print(
            f" {mark} {info.name:<36} {format_size(info.size):>9}  {info.description}"
        )
    print("\n(* = downloaded)\n")


def _cli_progress(done: int, total: int) -> None:
    width = 40
    filled = int(width * done / total) if total else 0
    sys.stdout.write(
        f"\r[{'=' * filled}{' ' * (width - filled)}] "
        f"{done * 100 // max(total, 1):3d}%  {format_size(done)} / {format_size(total)}"
    )
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(f"Usage: {Path(sys.argv[0]).name} <model> [models_path]")
        _list_models()
        return 1

    name = argv[0]
    models_dir = Path(argv[1]) if len(argv) > 1 else MODELS_DIR
    if name not in MODELS:
        print(f"Invalid model: {name}")
        _list_models(models_dir)
        return 1

    info = MODELS[name]
    print(f"Downloading {name} ({format_size(info.size)}) from '{info.url}' ...")
    if is_downloaded(name, models_dir):
        print(f"Model {name} already exists. Skipping download.")
        return 0
    try:
        path = download_model(name, models_dir, progress=_cli_progress)
    except KeyboardInterrupt:
        print("\nCancelled. Run again to resume.")
        return 1
    except DownloadError as e:
        print(f"\n{e}")
        return 1
    print(f"\nDone! Model '{name}' saved in '{path}'")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
