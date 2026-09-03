#!/usr/bin/env bash
set -euo pipefail

printf 'YTLoad - Linux setup\n\n'

install_base() {
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 ffmpeg curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 ffmpeg curl ca-certificates
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --needed python ffmpeg curl ca-certificates
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper --non-interactive install python3 ffmpeg curl ca-certificates
  else
    echo 'Unsupported package manager. Install Python 3.10+, ffmpeg and curl manually.' >&2
    exit 1
  fi
}

install_base
mkdir -p "$HOME/.local/bin"

if command -v yt-dlp >/dev/null 2>&1; then
  echo 'Updating yt-dlp...'
  yt-dlp -U || true
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
  echo 'Installing current yt-dlp standalone release...'
  curl -fL --retry 3 \
    'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp' \
    -o "$HOME/.local/bin/yt-dlp"
  chmod 0755 "$HOME/.local/bin/yt-dlp"
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    echo
    echo 'Add this to your shell profile if ~/.local/bin is not already on PATH:'
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    ;;
esac

echo
if command -v deno >/dev/null 2>&1; then
  echo "Deno detected: $(deno --version | head -n 1)"
else
  echo 'Deno is optional but recommended for YouTube JS challenges.'
  echo 'Install it using your distribution package manager or https://deno.com/'
fi

echo
echo 'Diagnostics:'
PATH="$HOME/.local/bin:$PATH" python3 "$(dirname "$0")/ytload.py" doctor || true
