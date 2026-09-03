#!/usr/bin/env bash
set -euo pipefail

printf 'YTLoad - macOS setup\n\n'

if ! command -v brew >/dev/null 2>&1; then
  echo 'Homebrew is required for this installer.'
  echo 'Install it from: https://brew.sh/'
  exit 1
fi

packages=(python yt-dlp ffmpeg deno)
for package in "${packages[@]}"; do
  if brew list --formula "$package" >/dev/null 2>&1; then
    echo "Updating $package..."
    brew upgrade "$package" || true
  else
    echo "Installing $package..."
    brew install "$package"
  fi
done

echo
echo 'Diagnostics:'
python3 "$(dirname "$0")/ytload.py" doctor || true

echo
echo 'Ready.'
echo "Run: python3 \"$(dirname "$0")/ytload.py\""
