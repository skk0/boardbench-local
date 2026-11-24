#!/bin/bash
set -e
cd "$(dirname "$0")"
if command -v python3.12 >/dev/null 2>&1; then
  interpreter="$(command -v python3.12)"
elif [ -x "$HOME/.local/bin/python3.12" ]; then
  interpreter="$HOME/.local/bin/python3.12"
else
  interpreter=python3
fi
if [ ! -x .venv-desktop/bin/python ]; then
  "$interpreter" -m venv .venv-desktop
fi
if ! .venv-desktop/bin/python -c 'import aiohttp, serial, esptool, keyring, webview' 2>/dev/null; then
  .venv-desktop/bin/python -m pip install -r requirements.txt
fi
exec .venv-desktop/bin/python app.py "$@"
