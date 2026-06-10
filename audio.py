"""Microphone capture. Records while push-to-talk is active, returns WAV bytes.

Both the Recorder and record_until_silence accept an optional on_level callback
that receives a float 0..1 (RMS) roughly 20x/sec. The UI visualiser uses this.
"""
import io
import shutil
import subprocess
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np

try:
    import sounddevice as sd
except Exception:
    sd = None

SAMPLE_RATE = 16000
CHANNELS = 1
LEVEL_HZ = 20  # visualiser tick rate
LEVEL_BLOCK = SAMPLE_RATE // LEVEL_HZ  # ~800 samples → 50ms windows
PCM_BYTES_PER_SAMPLE = 2

LevelCallback = Callable[[float], None]


class MicNotFoundError(RuntimeError):
    pass


def _sounddevice_available() -> bool:
    if sd is None:
        return False
    try:
        sd.check_input_settings(samplerate=SAMPLE_RATE, channels=CHANNELS)
        return True
    except Exception:
        return False


def _arecord_available() -> bool:
    return shutil.which("arecord") is not None


def _pcm16_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> tuple[bytes, float]:
    if not pcm:
        return b"", 0.0
    duration = len(pcm) / (sample_rate * CHANNELS * PCM_BYTES_PER_SAMPLE)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(PCM_BYTES_PER_SAMPLE)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue(), duration


def _rms(chunk: np.ndarray) -> float:
    if chunk.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(chunk))))


class Recorder:
    """Push-to-talk recorder. start() begins capture, stop() returns WAV bytes.

    Pass on_level=callback to receive real-time RMS levels (0..1) at ~20Hz.
    Callback is invoked from the sounddevice audio thread — keep it cheap.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        on_level: Optional[LevelCallback] = None,
    ):
        self.sample_rate = sample_rate
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._arecord_proc: subprocess.Popen | None = None
        self._arecord_thread: threading.Thread | None = None
        self._pcm_chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._recording = False
        self._on_level = on_level
        self._level_buf = np.empty(0, dtype=np.float32)

    def set_level_callback(self, cb: Optional[LevelCallback]):
        self._on_level = cb

    def _emit_levels(self, indata: np.ndarray):
        if self._on_level is None:
            return
        self._level_buf = np.concatenate([self._level_buf, indata.flatten()])
        while self._level_buf.size >= LEVEL_BLOCK:
            block = self._level_buf[:LEVEL_BLOCK]
            self._level_buf = self._level_buf[LEVEL_BLOCK:]
            try:
                self._on_level(_rms(block))
            except Exception:
                pass

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass
        with self._lock:
            if self._recording:
                self._chunks.append(indata.copy())
                self._emit_levels(indata)

    def start(self):
        if self._recording:
            return

        with self._lock:
            self._chunks = []
            self._pcm_chunks = []
            self._recording = True
            self._level_buf = np.empty(0, dtype=np.float32)

        if _sounddevice_available():
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=CHANNELS,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
            return

        if _arecord_available():
            self._start_arecord()
            return

        with self._lock:
            self._recording = False
        raise MicNotFoundError("No supported microphone backend found")

    def _start_arecord(self):
        cmd = [
            "arecord",
            "-q",
            "-f", "S16_LE",
            "-r", str(self.sample_rate),
            "-c", str(CHANNELS),
            "-t", "raw",
            "-",
        ]
        self._arecord_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._arecord_thread = threading.Thread(
            target=self._read_arecord,
            name="arecord-reader",
            daemon=True,
        )
        self._arecord_thread.start()

    def _read_arecord(self):
        proc = self._arecord_proc
        if proc is None or proc.stdout is None:
            return
        block_bytes = LEVEL_BLOCK * PCM_BYTES_PER_SAMPLE
        while True:
            with self._lock:
                recording = self._recording
            if not recording:
                break
            data = proc.stdout.read(block_bytes)
            if not data:
                break
            with self._lock:
                self._pcm_chunks.append(data)
            if self._on_level is not None:
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                try:
                    self._on_level(_rms(samples))
                except Exception:
                    pass

    def stop(self) -> tuple[bytes, float]:
        """Stop recording. Returns (wav_bytes, duration_seconds). wav_bytes is empty if nothing recorded."""
        with self._lock:
            self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._arecord_proc is not None:
            try:
                self._arecord_proc.terminate()
                self._arecord_proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._arecord_proc.kill()
            except Exception:
                pass
            self._arecord_proc = None
        if self._arecord_thread is not None:
            self._arecord_thread.join(timeout=1.0)
            self._arecord_thread = None

        with self._lock:
            pcm_chunks = self._pcm_chunks
            self._pcm_chunks = []
        if pcm_chunks:
            return _pcm16_to_wav(b"".join(pcm_chunks), self.sample_rate)

        with self._lock:
            chunks = self._chunks
            self._chunks = []

        if not chunks:
            return b"", 0.0

        audio = np.concatenate(chunks, axis=0).flatten()
        duration = len(audio) / self.sample_rate
        if duration <= 0:
            return b"", 0.0

        pcm16 = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm16 * 32767.0).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm16.tobytes())
        return buf.getvalue(), duration

    @property
    def is_recording(self) -> bool:
        return self._recording


def record_until_silence(
    silence_seconds: float = 1.5,
    silence_threshold: float = 0.01,
    max_seconds: float = 30.0,
    stop_event: threading.Event | None = None,
    on_level: Optional[LevelCallback] = None,
) -> tuple[bytes, float]:
    """Record until <silence_seconds> of audio below RMS threshold, or stop_event set, or max_seconds reached."""
    if _sounddevice_available():
        return _record_until_silence_sounddevice(
            silence_seconds=silence_seconds,
            silence_threshold=silence_threshold,
            max_seconds=max_seconds,
            stop_event=stop_event,
            on_level=on_level,
        )
    if _arecord_available():
        return _record_until_silence_arecord(
            silence_seconds=silence_seconds,
            silence_threshold=silence_threshold,
            max_seconds=max_seconds,
            stop_event=stop_event,
            on_level=on_level,
        )
    raise MicNotFoundError("No supported microphone backend found")


def _record_until_silence_sounddevice(
    silence_seconds: float,
    silence_threshold: float,
    max_seconds: float,
    stop_event: threading.Event | None,
    on_level: Optional[LevelCallback],
) -> tuple[bytes, float]:

    chunks: list[np.ndarray] = []
    block_duration = 1.0 / LEVEL_HZ
    block_size = LEVEL_BLOCK
    silence_accumulated = 0.0
    total_duration = 0.0
    saw_speech = False

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        blocksize=block_size,
    ) as stream:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            data, _ = stream.read(block_size)
            chunks.append(data.copy())
            rms = _rms(data)
            if on_level is not None:
                try:
                    on_level(rms)
                except Exception:
                    pass
            total_duration += block_duration

            if rms >= silence_threshold:
                saw_speech = True
                silence_accumulated = 0.0
            else:
                silence_accumulated += block_duration

            if saw_speech and silence_accumulated >= silence_seconds:
                break
            if total_duration >= max_seconds:
                break

    if not chunks:
        return b"", 0.0

    audio = np.concatenate(chunks, axis=0).flatten()
    duration = len(audio) / SAMPLE_RATE
    pcm16 = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue(), duration


def _record_until_silence_arecord(
    silence_seconds: float,
    silence_threshold: float,
    max_seconds: float,
    stop_event: threading.Event | None,
    on_level: Optional[LevelCallback],
) -> tuple[bytes, float]:
    cmd = [
        "arecord",
        "-q",
        "-f", "S16_LE",
        "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS),
        "-t", "raw",
        "-",
    ]
    block_bytes = LEVEL_BLOCK * PCM_BYTES_PER_SAMPLE
    silence_accumulated = 0.0
    total_duration = 0.0
    saw_speech = False
    chunks: list[bytes] = []

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if proc.stdout is None:
                break
            data = proc.stdout.read(block_bytes)
            if not data:
                break
            chunks.append(data)
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            rms = _rms(samples)
            if on_level is not None:
                try:
                    on_level(rms)
                except Exception:
                    pass

            block_duration = len(samples) / SAMPLE_RATE
            total_duration += block_duration
            if rms >= silence_threshold:
                saw_speech = True
                silence_accumulated = 0.0
            else:
                silence_accumulated += block_duration

            if saw_speech and silence_accumulated >= silence_seconds:
                break
            if total_duration >= max_seconds:
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass

    return _pcm16_to_wav(b"".join(chunks), SAMPLE_RATE)
