# FlowClone

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Platform: Windows | Linux](https://img.shields.io/badge/Platform-Windows%2011%20%7C%20Linux-lightgrey.svg)

An open-source voice-dictation tool for Windows and Linux in the spirit of Wispr Flow. Hold a hotkey, speak, and cleaned-up text appears in whatever app you're typing into. Bring your own API key — **OpenAI** (paid) or **Groq** (free tier, no credit card) — or run fully offline with **whisper.cpp** and a **local LLM**.

## What it does

- **Push-to-talk dictation** — hold `Right Alt`, speak, release. Whisper transcribes, an LLM cleans up filler words and punctuation, result is pasted at the cursor.
- **Command Mode** — select text anywhere, press `Ctrl+Shift+Space`, say something like *"make this more formal"* or *"turn this into bullet points"*, and the selection is rewritten in place.
- **Glass overlay** — bottom-centre floating pill with a live audio visualiser, doesn't steal focus. Shows *Listening → Transcribing → AI processing → Done*.
- **Mix and match providers** — pick the transcription provider (OpenAI, Groq, local whisper.cpp) and the AI processing provider (OpenAI, Groq, local LLM) independently.
- **Fully offline mode** — transcribe with whisper.cpp and clean up with a local LLM; no API keys, no internet. Download whisper.cpp models from Settings and FlowClone runs and supervises a local `whisper-server` for you.
- **Settings** — `Ctrl+Shift+F` (or right-click the tray icon). Includes microphone selection.

## Why FlowClone?

- **Works fully offline.** Pair *Local whisper.cpp* with a *Local LLM* (Ollama, llama.cpp, LM Studio…) and your audio and text never leave your machine — no account, no API key, no internet connection needed. See [Local transcription](#local-transcription-whispercpp) and [Local LLM](#local-llm).
- **Free to run** on Groq's free tier (whisper-large-v3-turbo + llama-3.3-70b-versatile), or at zero cost offline.
- **Your data, your choice** — you pick the providers, you hold the API keys.
- **No telemetry, no analytics.** Nothing leaves your machine except the audio/text you explicitly record, which goes directly to the provider of your choice.
- **Open source** (MIT). Fork it, swap the LLM, restyle the overlay.

## Install

### Option A — Download the EXE (recommended)

1. Go to [**Releases**](../../releases/latest).
2. Download `FlowClone.exe`.
3. Double-click to launch. A microphone icon will appear in your system tray.
4. Right-click the tray icon → **Settings** (or press `Ctrl+Shift+F`) to paste your API key and pick a provider.

Windows SmartScreen may warn about running an unsigned EXE — this is expected for small open-source projects without code-signing certificates. Click **More info → Run anyway** if you trust the source. You can also verify the binary by building it yourself (Option B).

User settings live in `%APPDATA%\FlowClone\` (your API keys in `.env`, preferences in `config.json`, custom vocab in `dictionary.txt`). The EXE itself is stateless.

### Option B — Run from source (Windows)

With [uv](https://docs.astral.sh/uv/):

```bat
git clone https://github.com/YOUR-USERNAME/flowclone
cd flowclone
uv run main.py
```

Or with plain pip:

```bat
git clone https://github.com/YOUR-USERNAME/flowclone
cd flowclone
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requires Python 3.11+. User settings live next to the source files (`.env`, `config.json`, `dictionary.txt` — all gitignored).

### Option C — Linux (from source)

Works on X11 and Wayland (tested on KDE Plasma / Wayland). Install the system packages first (Debian/Ubuntu names):

```sh
sudo apt install libportaudio2 libxcb-cursor0 wl-clipboard xclip
# optional: xdotool (X11) or kdotool (KDE Wayland) to detect terminals,
# which paste with Ctrl+Shift+V instead of Ctrl+V
```

Global hotkeys are read from `/dev/input` and text is typed through a `/dev/uinput` virtual keyboard, so your user needs access to both:

```sh
sudo usermod -aG input $USER
# if /dev/uinput isn't group-accessible on your distro:
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Log out and back in, then:

```sh
git clone https://github.com/YOUR-USERNAME/flowclone
cd flowclone
uv run main.py        # or: pip install -r requirements.txt && python main.py
```

On Wayland the Qt UI runs through XWayland (`QT_QPA_PLATFORM=xcb`) so the overlay can position itself and stay on top; override by setting `QT_QPA_PLATFORM` yourself. The tray icon uses Qt's `QSystemTrayIcon`. Press `Ctrl+C` in the terminal to quit cleanly.

### Pick providers

Transcription and AI processing (cleanup + Command Mode) are configured separately in Settings, so you can e.g. transcribe locally and clean up with Groq.

| Provider | Transcription | AI processing | Key |
| --- | --- | --- | --- |
| **Groq** (free) | `whisper-large-v3-turbo` | `llama-3.3-70b-versatile` | `gsk_…` from [console.groq.com](https://console.groq.com) — generous free tier, no credit card |
| **OpenAI** (paid) | `whisper-1` | `gpt-4o-mini` | [platform.openai.com](https://platform.openai.com), pay-as-you-go |
| **Local whisper.cpp** | any ggml Whisper model | — | none |
| **Local LLM** | — | any OpenAI-compatible server | none |

Paste your key(s) into Settings (`Ctrl+Shift+F` or right-click tray → Settings), pick your providers, done. API key fields only appear for the providers you've selected.

### Local transcription (whisper.cpp)

1. Build whisper.cpp once (needs git, CMake and a C/C++ compiler). The binaries land in `bin/`:

   ```sh
   uv run scripts/build_whisper_cpp.py                    # CPU build
   uv run scripts/build_whisper_cpp.py -DGGML_VULKAN=ON   # or a GPU build (Vulkan, -DGGML_CUDA=ON, …)
   ```

2. In Settings, choose a model under *Local transcription (whisper.cpp)* and click **Download**. Models are stored in `whisper-models/` and verified against their SHA-256; interrupted downloads resume. (CLI alternative: `uv run whispercpp.py <model>`; run without arguments to list models.)
3. Set *Transcription provider* to **Local whisper.cpp** and save.

FlowClone then starts `whisper-server` on `127.0.0.1` in the background, restarts it if it crashes, restarts it with the new model when you switch models, and stops it on exit. Server output goes to `whisper-server.log`. For the packaged EXE, put the binaries in `%APPDATA%\FlowClone\bin\`.

### Local LLM

Set *AI processing provider* to **Local LLM** and enter the URL and model of any OpenAI-compatible chat endpoint — e.g. [Ollama](https://ollama.com) (`http://localhost:11434/v1`, default), llama.cpp's `llama-server` (`http://localhost:8080/v1`) or LM Studio.

## Hotkeys

| Action | Default |
| --- | --- |
| Push-to-talk dictation | Hold `Right Alt` |
| Command Mode | `Ctrl + Shift + Space` |
| Open Settings | `Ctrl + Shift + F` |

All three are reconfigurable in Settings.

## Privacy & Permissions

FlowClone runs entirely on your machine. No telemetry, no analytics, no usage reporting. The only network traffic is to the providers you configure for the audio/text you explicitly record — and with **Local whisper.cpp** + **Local LLM** there is none at all (apart from downloading models).

**What gets sent off your machine**
- Your recorded audio → your transcription provider (OpenAI or Groq). With local whisper.cpp it goes to `whisper-server` on `127.0.0.1` only.
- The raw transcript (and, in Command Mode, the selected text) → your AI processing provider (OpenAI, Groq, or the local LLM URL you entered).
- Your API key → sent as a Bearer token with each request to that provider; stored locally in `.env`.

Both cloud providers have their own data-retention policies — check them before using with sensitive content. As of writing, Groq states that API inputs/outputs are not used for training; OpenAI retains API audio for up to 30 days for abuse monitoring unless you're on a zero-retention plan.

**System-level access FlowClone needs**
- **Microphone** — recorded *only while* push-to-talk is held, or while Command Mode is listening. No always-on recording.
- **Global keyboard hook** — so hotkeys work even when FlowClone isn't focused. On Windows this uses a standard low-level keyboard hook; on Linux it reads `/dev/input/event*` via evdev (without grabbing the devices). The hook receives all key events but FlowClone only reacts to its configured hotkeys; other keystrokes are not stored, logged, or transmitted.
- **Synthetic keystrokes** — to paste. On Linux these go through a `/dev/uinput` virtual keyboard named `flowclone-virtual-keyboard`.
- **Clipboard** — briefly written to (to insert text via Ctrl+V) and, in Command Mode, read from (via Ctrl+C to capture your selection). Original clipboard content is restored after each injection.

**Files written on your machine**

When running the downloaded EXE, user data lives in `%APPDATA%\FlowClone\`:

- `.env` — your API keys.
- `config.json` — preferences (hotkeys, language, providers, microphone, whisper.cpp model, local LLM URL). No sensitive data.
- `dictionary.txt` — your custom vocabulary for Whisper prompt biasing (may contain personal or proprietary terms).
- `whisper-models/` — downloaded whisper.cpp models (only if you use local transcription).
- `whisper-server.log` — output of the local whisper-server.

When running from source, the same files live next to `main.py` (all gitignored — they never reach the repo, apart from the default `config.json`).

`config.json` and `dictionary.txt` are read from next to the executable (or `main.py`) first. On Linux, if they aren't there, `$XDG_CONFIG_HOME/flowclone/` (default `~/.config/flowclone/`) is used, and that's where new ones are created.

## Admin rights (Windows)

Linux has no equivalent restriction — access to `/dev/input` and `/dev/uinput` (see *Option C*) is all FlowClone needs there.

Windows User Interface Privilege Isolation (UIPI) **blocks a non-admin process from typing into an admin-level window**. So if FlowClone is running normally and your cmd / PowerShell / Windows Terminal is elevated, push-to-talk will record and transcribe but the text will never arrive — the `SendInput` call is silently rejected.

FlowClone detects this. When injection is blocked you'll see *"Target window is elevated — right-click tray → Run as admin"*, and the tray icon gains a **Run as administrator** item that relaunches via UAC. Once FlowClone is elevated, push-to-talk works into any terminal (elevated or not). The tray header reads *FlowClone (admin)* when running elevated.

If you routinely use admin terminals, the simplest setup is to always launch FlowClone as admin — right-click `FlowClone.exe` → *Run as administrator*, or set that as the default under *Properties → Compatibility → Run this program as an administrator*. For everyday non-admin apps (browsers, Word, Outlook, most editors) normal FlowClone is fine — no elevation needed.

## How the flow works

```
Hotkey press   →  Overlay "Listening"   →  Mic capture
Hotkey release →  Overlay "Transcribing"   →  Whisper
               →  Overlay "AI processing"  →  LLM cleanup
               →  Clipboard inject (Ctrl+V)  →  Overlay "Done" (fades)
```

The overlay is a small always-on-top pill centred at the bottom of the screen. It doesn't steal focus — whatever text field you were typing in stays active.

## Custom dictionary

Add one term per line in Settings → Custom dictionary. Those terms are passed to Whisper as a prompt hint so unusual spellings (product names, acronyms, jargon) come out right. Your `dictionary.txt` is gitignored, so forks and pushes won't expose your terms.

If you want to see the format, see `dictionary.example.txt` in the repo.

## Building the EXE yourself

```bat
pip install pyinstaller
build.bat
```

The executable lands at `dist\FlowClone.exe`. `build.bat` regenerates the icon from `tray.py`, then runs PyInstaller with the right hidden-imports. CI runs the same script on `windows-latest` when a version tag is pushed (see `.github/workflows/release.yml`).

## Files

```
flowclone/
├── main.py                     Entry point; wires hotkeys, overlay, tray, Qt loop
├── config.py                   Config + provider abstraction + secret scrubbing
├── hotkeys.py                  Global hotkey listener
├── keyhook.py                  Low-level keyboard hook (Windows)
├── keyhook_linux.py            evdev keyboard hook + hotkey combos (Linux)
├── audio.py                    Mic capture, device selection, live amplitude
├── transcription.py            Whisper transcription (OpenAI, Groq or whisper.cpp)
├── whispercpp.py               whisper.cpp model downloader + whisper-server supervisor
├── ai_processor.py             LLM cleanup + command-mode transform
├── injector.py                 Clipboard-based text injection (Windows)
├── injector_linux.py           Clipboard + uinput text injection (Linux)
├── elevation.py                Admin / UIPI detection and relaunch
├── autostart.py                Start at login (registry / XDG autostart)
├── command_mode.py             Command Mode pipeline
├── overlay.py                  Glass pill status widget (bottom-centre)
├── visualiser.py               Live 16-bar audio visualiser
├── tray.py                     System tray icon + menu (pystray / Qt)
├── settings_window.py          Dark-themed settings dialog
├── assets/flowclone.ico        EXE icon (generated from tray.py)
├── scripts/generate_icon.py    Regenerates the ICO from the tray glyph
├── scripts/build_whisper_cpp.py  Downloads + compiles whisper.cpp into bin/
├── dictionary.example.txt      Template for your custom vocabulary
├── .env.example                Template for API keys
├── config.json                 Default preferences
├── pyproject.toml              Project + dependencies (for uv)
├── requirements.txt            Same dependencies for pip
├── build.bat                   Local PyInstaller build
├── .github/workflows/          CI builds EXE on version tag push
├── LICENSE                     MIT
└── SECURITY.md                 How to report security issues
```

## Contributing

PRs welcome. If you're planning something big, open an issue first so we can align. See `SECURITY.md` for how to report vulnerabilities privately.

## Troubleshooting

- **"No API key for …"** — paste your key in Settings; it's written to `.env`. The overlay also shows *No API key* in red while recording if the selected transcription or AI provider has no key. Local providers need none.
- **"No microphone detected"** — Windows Settings → Privacy → Microphone → allow desktop apps. On Linux, check the device is selected in Settings → Microphone and isn't muted in your sound settings.
- **Visualiser doesn't move** — pick the right input in Settings → Microphone.
- **"Can't read /dev/input" (Linux)** — add your user to the `input` group and log in again (see *Option C*).
- **Hotkeys fire but nothing pastes (Linux)** — check write access to `/dev/uinput` and that `wl-clipboard` (Wayland) or `xclip` (X11) is installed.
- **"Model … not downloaded" / "whisper-server not found"** — download the model in Settings, and build whisper.cpp with `scripts/build_whisper_cpp.py`. Check `whisper-server.log` if transcription still fails.
- **"AI failed" with Local LLM** — make sure the server at the configured URL is running and the model name exists there (e.g. `ollama pull llama3.2`).
- **Push-to-talk works but nothing pastes** — the active app may block simulated Ctrl+V. Try a different app to confirm. (Console windows — cmd.exe, PowerShell, Windows Terminal — are auto-detected and typed into directly, so Ctrl+V setting doesn't matter there.)
- **Hotkeys don't fire in one specific app** — that app is probably running elevated. Relaunch FlowClone from an elevated terminal.
- **Overlay appears but text never arrives** — check network; a transcription failure shows "Transcription failed" briefly, then the overlay fades.
- **Overlay doesn't appear at all in one specific app** — that app may be claiming topmost z-order. FlowClone re-asserts topmost on every show since v0.1.3, which fixes most cases (notably Windows Terminal / cmd / PowerShell). If you still hit it, set `FLOWCLONE_DEBUG=1` before launching and the app will log every hotkey event + foreground-window class to `debug.log` (next to `.env` — i.e. `%APPDATA%\FlowClone\debug.log` for the installed EXE, or next to `main.py` for source runs). Share that file in an issue.
- **Overlay says "Target window is elevated — right-click tray → Run as admin"** — the focused window is running at High integrity (e.g. an admin cmd) and FlowClone isn't. Click the tray option to relaunch elevated; see *Admin rights* above.

## License

MIT — see [LICENSE](LICENSE).
