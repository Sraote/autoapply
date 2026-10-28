#!/usr/bin/env bash
# autoapply setup for Arch Linux (Wayland/Hyprland, Sway, GNOME, KDE, or X11).
# Usage: ./scripts/setup_arch.sh [--system-chromium]
#   --system-chromium  use pacman's chromium instead of Playwright's bundled build
set -euo pipefail
cd "$(dirname "$0")/.."

USE_SYSTEM_CHROMIUM=0
[[ "${1:-}" == "--system-chromium" ]] && USE_SYSTEM_CHROMIUM=1

echo "==> Installing system packages (sudo)"
# `playwright install-deps` only supports Debian/Ubuntu, so these are the pacman equivalents
# of Chromium's runtime libraries, plus notification and sound tools.
PKGS=(
  python uv git
  nss nspr at-spi2-core libcups libdrm libxkbcommon libxcomposite libxdamage
  libxrandr libxfixes libxshmfence mesa pango cairo alsa-lib
  libnotify               # notify-send fallback for desktop alerts
  sound-theme-freedesktop # the ping sound (played with pw-play / paplay)
  noto-fonts              # avoids tofu boxes in headless screenshots
)
[[ $USE_SYSTEM_CHROMIUM == 1 ]] && PKGS+=(chromium)
sudo pacman -S --needed --noconfirm "${PKGS[@]}"

echo "==> Creating virtualenv and installing dependencies"
uv sync

if [[ $USE_SYSTEM_CHROMIUM == 1 ]]; then
  echo "==> Using /usr/bin/chromium — set browser.executable_path: /usr/bin/chromium in config/config.yaml"
else
  echo "==> Downloading Playwright's Chromium"
  uv run playwright install chromium
fi

echo "==> Creating config files"
uv run autoapply init

if git rev-parse --git-dir >/dev/null 2>&1; then
  echo "==> Installing git pre-commit hooks (secret + resume guard)"
  uv run --with pre-commit pre-commit install
fi

echo
uv run autoapply doctor
cat <<'EOF'

Done. Hyprland/Wayland notes:
  * Headed Chromium runs natively on Wayland (autoapply passes --ozone-platform-hint=auto).
  * Desktop notifications need a notification daemon (mako, dunst, swaync, …) running.
  * If prompts should raise the browser, add a windowrule so Chromium may take focus, e.g.
      windowrulev2 = focusonactivate, class:^(chromium|Chromium)$
EOF
