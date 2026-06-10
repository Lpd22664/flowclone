#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

XCB_RUNTIME="$PWD/.venv/xcb-runtime/usr/lib/x86_64-linux-gnu"
if [[ -n "${DISPLAY:-}" && -r "$XCB_RUNTIME/libxcb-cursor.so.0" ]]; then
  export LD_LIBRARY_PATH="$XCB_RUNTIME${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export QT_QPA_PLATFORM=xcb
fi

exec .venv/bin/python main.py
