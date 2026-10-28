#!/usr/bin/env bash
# autoapply installer for Linux (release bundle).
#   ./install.sh                 install / upgrade
# Env:
#   SKIP_SYSTEM_DEPS=1           don't run pacman for Chromium's libraries
#   AUTOAPPLY_SKIP_BROWSER=1     don't download Chromium now (run `autoapply install-browser` later)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEEL="$(ls "$HERE"/autoapply-*.whl | head -n1)"

echo "==> autoapply installer ($(basename "$WHEEL"))"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv (Python package manager; it also provides Python itself)"
  if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

if command -v pacman >/dev/null 2>&1 && [[ "${SKIP_SYSTEM_DEPS:-0}" != 1 ]]; then
  echo "==> Installing Chromium runtime libraries with pacman (sudo)"
  sudo pacman -S --needed --noconfirm \
    nss nspr at-spi2-core libcups libdrm libxkbcommon libxcomposite libxdamage \
    libxrandr libxfixes libxshmfence mesa pango cairo alsa-lib \
    libnotify sound-theme-freedesktop noto-fonts
fi

echo "==> Installing autoapply as an isolated tool"
uv tool install --force --python 3.12 "$WHEEL"
uv tool update-shell >/dev/null 2>&1 || true
BIN="$(uv tool dir --bin)"
export PATH="$BIN:$PATH"

if [[ "${AUTOAPPLY_SKIP_BROWSER:-0}" != 1 ]]; then
  echo "==> Downloading Chromium for Playwright"
  autoapply install-browser
fi

echo "==> Creating your workspace (~/autoapply)"
autoapply init

cat <<EOF

Installed: $BIN/autoapply
Open a new terminal (so PATH is updated), then:
  1. edit ~/autoapply/config/profile_data.yaml and ~/autoapply/config/config.yaml
  2. copy your resume to ~/autoapply/data/private/resume.pdf
  3. autoapply doctor && autoapply profile build
Upgrade: run a newer bundle's install.sh.   Uninstall: uv tool uninstall autoapply
EOF
