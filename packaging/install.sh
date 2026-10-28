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

echo "==> Adding autoapply to your app launcher"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$ICON_DIR" "$APP_DIR"
cp "$HERE/autoapply.svg" "$ICON_DIR/autoapply.svg"
cat > "$APP_DIR/autoapply.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=autoapply
GenericName=Job application assistant
Comment=Apply to internships and entry-level roles, pausing for your input
Exec=$BIN/autoapply-gui
Icon=autoapply
Terminal=false
Categories=Office;Utility;
StartupWMClass=autoapply
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true

cat <<EOF

Installed. Open "autoapply" from your app launcher (or run: autoapply-gui).
Start on the "Your profile" page: add your resume and fill in your details.
Command line is still available: autoapply --help (open a new terminal first).
Upgrade: run a newer bundle's install.sh.
Uninstall: uv tool uninstall autoapply && rm "$APP_DIR/autoapply.desktop" "$ICON_DIR/autoapply.svg"
EOF
