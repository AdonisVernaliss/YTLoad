#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 ytload.py --ui
fi
printf 'Python 3.10 or newer is required. Run install-macos.sh first.\n'
read -r answer
