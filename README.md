# FlowClone

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2011-lightgrey.svg)

An open-source Windows voice-dictation tool in the spirit of Wispr Flow. Hold a hotkey, speak, and cleaned-up text appears in whatever app you're typing into. Bring your own API key — **OpenAI** (paid) or **Groq** (free tier, no credit card).

## What it does

- **Push-to-talk dictation** — hold `Right Alt`, speak, release. Whisper transcribes, an LLM cleans up filler words and punctuation, result is pasted at the cursor.
- **Command Mode** — select text anywhere, press `Ctrl+Shift+Space`, say something like *"make this more formal"* or *"turn this into bullet points"*, and the selection is rewritten in place.
- **Glass overlay** — bottom-centre floating pill with a live audio visualiser, doesn't steal focus.
- **Settings** — `Ctrl+Shift+F` (or right-click the tray icon).

## Why FlowClone?

- **Free to run** on Groq's free tier (whisper-large-v3-turbo + llama-3.3-70b-versatile).
- **Your data, your choice** — you pick the provider, you hold the API key.
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

### Option B — Run from source

```bat
git clone https://github.com/YOUR-USERNAME/flowclone
cd flowclone
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Requires Python 3.11+. User settings live next to the source files (`.env`, `config.json`, `dictionary.txt` — all gitignored).

### Pick a provider

- **Groq** (free) — sign up at [console.groq.com](https://console.groq.com), mint an API key (`gsk_…`). Free tier has generous rate limits, no credit card. Uses `whisper-large-v3-turbo` + `llama-3.3-70b-versatile`.
- **OpenAI** (paid) — [platform.openai.com](https://platform.openai.com). Pay-as-you-go. Uses `whisper-1` + `gpt-4o-mini`.

Paste your key into Settings (`Ctrl+Shift+F` or right-click tray → Settings), pick your provider, done.

## Hotkeys

| Action | Default |
| --- | --- |
| Push-to-talk dictation | Hold `Right Alt` |
| Command Mode | `Ctrl + Shift + Space` |
| Open Settings | `Ctrl + Shift + F` |

All three are reconfigurable in Settings.

## Privacy & Permissions

FlowClone runs entirely on your machine. No telemetry, no analytics, no usage reporting. The only network traffic is to the LLM provider you configure (OpenAI or Groq) for the audio/text you explicitly record.

**What gets sent off your machine**
- Your recorded audio → your selected provider (OpenAI or Groq) for transcription.
- The raw transcript → the same provider for cleanup.
- Your API key → sent as a Bearer token with each request; stored locally in `.env`.

Both providers have their own data-retention policies — check them before using with sensitive content. As of writing, Groq states that API inputs/outputs are not used for training; OpenAI retains API audio for up to 30 days for abuse monitoring unless you're on a zero-retention plan.

**System-level access FlowClone needs**
- **Microphone** — recorded *only while* push-to-talk is held, or while Command Mode is listening. No always-on recording.
- **Global keyboard hook** — so hotkeys work even when FlowClone isn't focused. Uses the `keyboard` library's standard Windows low-level hook. The hook receives all key events but FlowClone only reacts to its configured hotkeys; other keystrokes are not stored, logged, or transmitted.
- **Clipboard** — briefly written to (to insert text via Ctrl+V) and, in Command Mode, read from (via Ctrl+C to capture your selection). Original clipboard content is restored after each injection.

**Files written on your machine**

When running the downloaded EXE, user data lives in `%APPDATA%\FlowClone\`:

- `.env` — your API keys.
- `config.json` — preferences (hotkeys, language, provider). No sensitive data.
- `dictionary.txt` — your custom vocabulary for Whisper prompt biasing (may contain personal or proprietary terms).

When running from source, the same files live next to `main.py` (all gitignored — they never reach the repo).

## Admin rights

The `keyboard` library's global hooks **require admin rights to intercept input inside other elevated processes** (Task Manager, elevated terminals, some games). For everyday apps — browsers, editors, chat clients, Word, Outlook — no elevation is needed. If push-to-talk doesn't work in a specific app, try running `python main.py` from an elevated terminal.

## How the flow works

```
Hotkey press   →  Overlay "Listening"   →  Mic capture
Hotkey release →  Overlay "Thinking"    →  Whisper  →  LLM cleanup
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
├── audio.py                    Mic capture + live amplitude callback
├── transcription.py            Whisper API call (OpenAI or Groq)
├── ai_processor.py             LLM cleanup + command-mode transform
├── injector.py                 Clipboard-based text injection
├── command_mode.py             Command Mode pipeline
├── overlay.py                  Glass pill status widget (bottom-centre)
├── visualiser.py               Live 16-bar audio visualiser
├── tray.py                     System tray icon + menu
├── settings_window.py          Dark-themed settings dialog
├── assets/flowclone.ico        EXE icon (generated from tray.py)
├── scripts/generate_icon.py    Regenerates the ICO from the tray glyph
├── dictionary.example.txt      Template for your custom vocabulary
├── .env.example                Template for API keys
├── config.json                 Default preferences
├── requirements.txt
├── build.bat                   Local PyInstaller build
├── .github/workflows/          CI builds EXE on version tag push
├── LICENSE                     MIT
└── SECURITY.md                 How to report security issues
```

## Contributing

PRs welcome. If you're planning something big, open an issue first so we can align. See `SECURITY.md` for how to report vulnerabilities privately.

## Troubleshooting

- **"No API key for …"** — paste your key in Settings; it's written to `.env`.
- **"No microphone detected"** — Windows Settings → Privacy → Microphone → allow desktop apps.
- **Push-to-talk works but nothing pastes** — the active app may block simulated Ctrl+V. Try a different app to confirm. (Console windows — cmd.exe, PowerShell, Windows Terminal — are auto-detected and typed into directly, so Ctrl+V setting doesn't matter there.)
- **Hotkeys don't fire in one specific app** — that app is probably running elevated. Relaunch FlowClone from an elevated terminal.
- **Overlay appears but text never arrives** — check network; a transcription failure shows "Transcription failed" briefly, then the overlay fades.
- **Overlay doesn't appear at all in one specific app** — that app may be claiming topmost z-order. FlowClone re-asserts topmost on every show since v0.1.3, which fixes most cases (notably Windows Terminal / cmd / PowerShell). If you still hit it, set `FLOWCLONE_DEBUG=1` before launching and the app will log every hotkey event + foreground-window class to `debug.log` (next to `.env` — i.e. `%APPDATA%\FlowClone\debug.log` for the installed EXE, or next to `main.py` for source runs). Share that file in an issue.

## License

MIT — see [LICENSE](LICENSE).
