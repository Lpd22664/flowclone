"""whisper.cpp ggml model catalogue, downloader and whisper-server supervisor.

Python port of whisper.cpp's models/download-ggml-model.sh. Models are stored
in <app dir>/whisper-models and verified against the SHA-256 published by
Hugging Face. `server` supervises a local whisper-server process (from
<app dir>/bin) that is used for offline transcription.

CLI usage (mirrors the bash script):
    python whispercpp.py <model> [models_path]
    python whispercpp.py            # list available models
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from config import APP_DIR

MODELS_DIR = APP_DIR / "whisper-models"
BIN_DIR = APP_DIR / "bin"
SERVER_LOG = APP_DIR / "whisper-server.log"

_SRC = "https://huggingface.co/ggerganov/whisper.cpp"
_SRC_TDRZ = "https://huggingface.co/akashmjn/tinydiarize-whisper.cpp"
_PFX = "resolve/main/ggml"

_CHUNK = 1 << 20  # 1 MiB
_RETRIES = 5
_RETRY_DELAY = 5.0

# name -> (size in bytes, sha256), from the Hugging Face repo metadata.
_MODEL_DATA: dict[str, tuple[int, str]] = {
    "tiny": (
        77691713,
        "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
    ),
    "tiny.en": (
        77704715,
        "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
    ),
    "tiny-q5_1": (
        32152673,
        "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7",
    ),
    "tiny.en-q5_1": (
        32166155,
        "c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b",
    ),
    "tiny-q8_0": (
        43537433,
        "c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca",
    ),
    "base": (
        147951465,
        "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
    ),
    "base.en": (
        147964211,
        "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
    ),
    "base-q5_1": (
        59707625,
        "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
    ),
    "base.en-q5_1": (
        59721011,
        "4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f",
    ),
    "base-q8_0": (
        81768585,
        "c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9",
    ),
    "small": (
        487601967,
        "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
    ),
    "small.en": (
        487614201,
        "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
    ),
    "small.en-tdrz": (
        487614184,
        "ceac3ec06d1d98ef71aec665283564631055fd6129b79d8e1be4f9cc33cc54b4",
    ),
    "small-q5_1": (
        190085487,
        "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    ),
    "small.en-q5_1": (
        190098681,
        "bfdff4894dcb76bbf647d56263ea2a96645423f1669176f4844a1bf8e478ad30",
    ),
    "small-q8_0": (
        264464607,
        "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f",
    ),
    "medium": (
        1533763059,
        "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
    ),
    "medium.en": (
        1533774781,
        "cc37e93478338ec7700281a7ac30a10128929eb8f427dda2e865faa8f6da4356",
    ),
    "medium-q5_0": (
        539212467,
        "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    ),
    "medium.en-q5_0": (
        539225533,
        "76733e26ad8fe1c7a5bf7531a9d41917b2adc0f20f2e4f5531688a8c6cd88eb0",
    ),
    "medium-q8_0": (
        823369779,
        "42a1ffcbe4167d224232443396968db4d02d4e8e87e213d3ee2e03095dea6502",
    ),
    "large-v1": (
        3094623691,
        "7d99f41a10525d0206bddadd86760181fa920438b6b33237e3118ff6c83bb53d",
    ),
    "large-v2": (
        3094623691,
        "9a423fe4d40c82774b6af34115b8b935f34152246eb19e80e376071d3f999487",
    ),
    "large-v2-q5_0": (
        1080732091,
        "3a214837221e4530dbc1fe8d734f302af393eb30bd0ed046042ebf4baf70f6f2",
    ),
    "large-v2-q8_0": (
        1656129691,
        "fef54e6d898246a65c8285bfa83bd1807e27fadf54d5d4e81754c47634737e8c",
    ),
    "large-v3": (
        3095033483,
        "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
    ),
    "large-v3-q5_0": (
        1081140203,
        "d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1",
    ),
    "large-v3-turbo": (
        1624555275,
        "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
    ),
    "large-v3-turbo-q5_0": (
        574041195,
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    ),
    "large-v3-turbo-q8_0": (
        874188075,
        "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1",
    ),
}


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size: int
    sha256: str

    @property
    def english_only(self) -> bool:
        return ".en" in self.name

    @property
    def quantization(self) -> Optional[str]:
        for q in ("q5_0", "q5_1", "q8_0"):
            if self.name.endswith(q):
                return q
        return None

    @property
    def diarization(self) -> bool:
        return self.name.endswith("-tdrz")

    @property
    def filename(self) -> str:
        return f"ggml-{self.name}.bin"

    @property
    def url(self) -> str:
        src = _SRC_TDRZ if self.diarization else _SRC
        return f"{src}/{_PFX}-{self.name}.bin"


MODELS: dict[str, ModelInfo] = {
    name: ModelInfo(name, size, sha) for name, (size, sha) in _MODEL_DATA.items()
}

ProgressCallback = Callable[[int, int], None]  # (bytes_done, bytes_total)


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


def format_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GiB"
    return f"{n / (1 << 20):.0f} MiB"


def model_path(name: str, models_dir: Optional[Path] = None) -> Path:
    return Path(models_dir or MODELS_DIR) / MODELS[name].filename


def is_downloaded(name: str, models_dir: Optional[Path] = None) -> bool:
    path = model_path(name, models_dir)
    return path.is_file() and path.stat().st_size == MODELS[name].size


def _open(url: str, offset: int):
    req = urllib.request.Request(url, headers={"User-Agent": "flowclone"})
    if offset:
        req.add_header("Range", f"bytes={offset}-")
    token = os.environ.get("HF_TOKEN")
    if token:
        # Unredirected: don't leak the token to the CDN host HF redirects to.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=30)


def download_model(
    name: str,
    models_dir: Optional[Path] = None,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """Download model `name` into models_dir (default: whisper-models).

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


def download_file(
    url: str,
    dest: Path,
    size: int,
    sha256: str,
    name: str,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """Download `url` to `dest`, resuming `dest`.part and verifying the SHA-256.
    `name` is only used in error messages."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    hasher = hashlib.sha256()
    done = 0
    if part.exists():
        with open(part, "rb") as f:
            while chunk := f.read(_CHUNK):
                hasher.update(chunk)
                done += len(chunk)
        if done > size:
            part.unlink()
            hasher, done = hashlib.sha256(), 0

    attempt = 0
    while done < size:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("Download cancelled")
        try:
            with _open(url, done) as resp:
                if done and resp.status != 206:
                    # Server ignored the Range header — start over.
                    hasher, done = hashlib.sha256(), 0
                with open(part, "ab" if done else "wb") as f:
                    if progress:
                        progress(done, size)
                    while chunk := resp.read(_CHUNK):
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled("Download cancelled")
                        f.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        attempt = 0
                        if progress:
                            progress(done, size)
            if done < size:
                raise DownloadError("connection closed early")
        except (urllib.error.URLError, OSError, DownloadError) as e:
            if isinstance(e, DownloadCancelled):
                raise
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                raise DownloadError(f"Failed to download {url}: {e}") from e
            attempt += 1
            if attempt >= _RETRIES:
                raise DownloadError(f"Failed to download model {name}: {e}") from e
            time.sleep(_RETRY_DELAY)

    if hasher.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(f"Checksum mismatch for model {name}; please retry")
    os.replace(part, dest)
    return dest


# --- whisper-server supervisor -------------------------------------------


class WhisperServerError(RuntimeError):
    pass


def server_binary() -> Path:
    return BIN_DIR / (
        "whisper-server.exe" if sys.platform == "win32" else "whisper-server"
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Never route localhost requests through an HTTP proxy from the environment.
_local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_MAX_RESTARTS = 3  # within _RESTART_WINDOW seconds before giving up
_RESTART_WINDOW = 60.0
_RESTART_DELAY = 1.0


class WhisperServer:
    """Runs whisper-server in the background and restarts it if it crashes."""

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
            raise WhisperServerError(f"whisper-server not found in {BIN_DIR}")
        if model not in MODELS or not is_downloaded(model):
            raise WhisperServerError(f"Model {model} is not downloaded")
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
            name="whisper-server-supervisor",
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
        threads = max(1, min(8, os.cpu_count() or 4))
        cmd = [
            str(server_binary()),
            "-m",
            str(model_path(self._model)),
            "--host",
            self._host,
            "--port",
            str(self._port),
            "-t",
            str(threads),
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
                    self.last_error = f"whisper-server keeps exiting (code {code}); see {SERVER_LOG.name}"
                    self._proc = None
                    self._port = None
                    return
                self.last_error = f"whisper-server exited (code {code}); restarting"
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

    def transcribe(
        self,
        wav_bytes: bytes,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        timeout: float = 300,
    ) -> str:
        if not self.wait_ready(timeout=120):
            raise WhisperServerError(self.last_error or "whisper-server is not running")
        fields = {"temperature": "0.0", "response_format": "json"}
        fields["language"] = language or "auto"
        if prompt:
            fields["prompt"] = prompt

        boundary = uuid.uuid4().hex
        parts = []
        for key, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n".encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
        )
        parts.append(wav_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"{self.url}/inference",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with _local_opener.open(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()
        except (urllib.error.URLError, OSError) as e:
            raise WhisperServerError(f"whisper-server request failed: {e}") from e
        try:
            data = json.loads(body)
        except ValueError as e:
            raise WhisperServerError("Invalid response from whisper-server") from e
        if "error" in data:
            raise WhisperServerError(f"whisper-server: {data['error']}")
        # Segments are newline-separated; dictation wants a single line.
        return " ".join(str(data.get("text", "")).split())


server = WhisperServer()
atexit.register(server.stop)


# --- CLI ------------------------------------------------------------------


def _list_models() -> None:
    print("\nAvailable models:", end="")
    model_class = ""
    for name in MODELS:
        this_class = name.split(".")[0].split("-")[0]
        if this_class != model_class:
            print("\n ", end="")
            model_class = this_class
        print(f" {name}", end="")
    print("\n")


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
        print("___________________________________________________________")
        print(".en = english-only -q5_[01] = quantized -tdrz = tinydiarize")
        return 1

    name = argv[0]
    models_dir = Path(argv[1]) if len(argv) > 1 else MODELS_DIR
    if name not in MODELS:
        print(f"Invalid model: {name}")
        _list_models()
        return 1

    info = MODELS[name]
    print(
        f"Downloading ggml model {name} ({format_size(info.size)}) from '{info.url}' ..."
    )
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
