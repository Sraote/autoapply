# Setup

## Arch Linux

### Automatic

```bash
git clone https://github.com/<you>/autoapply.git && cd autoapply
./scripts/setup_arch.sh                 # or: ./scripts/setup_arch.sh --system-chromium
```

### Manual

1. **System packages.** `playwright install-deps` only knows apt, so install Chromium's libraries
   with pacman:
   ```bash
   sudo pacman -S --needed python uv git \
     nss nspr at-spi2-core libcups libdrm libxkbcommon libxcomposite libxdamage \
     libxrandr libxfixes libxshmfence mesa pango cairo alsa-lib \
     libnotify sound-theme-freedesktop noto-fonts
   ```
2. **Python environment.**
   ```bash
   uv sync
   ```
3. **Browser.** Either Playwright's build:
   ```bash
   uv run playwright install chromium
   ```
   or the distro's (`sudo pacman -S chromium`) with `browser.executable_path: /usr/bin/chromium`
   in `config/config.yaml`. The Playwright build is the tested path; distro Chromium can drift
   ahead of what your Playwright version expects.
4. **Config.** `uv run autoapply init`, edit both YAML files, put your resume at
   `data/private/resume.pdf`, then `uv run autoapply doctor`.

### Wayland / Hyprland notes

- Headed runs pass `--ozone-platform-hint=auto` when `WAYLAND_DISPLAY` is set, so Chromium runs
  natively instead of via XWayland. On X11 nothing special happens.
- Notifications need a running daemon (mako, dunst, swaync, or your DE's). Test with
  `notify-send hello`.
- Hyprland blocks focus stealing by default. To let a pause raise the browser:
  `windowrulev2 = focusonactivate, class:^(chromium|Chromium)$`
- Sound uses `pw-play` (PipeWire) or `paplay`; with neither, the terminal bell rings.
- Headless runs need no display at all (works over SSH or in a TTY).

## Windows 11

### Automatic

```powershell
git clone https://github.com/<you>/autoapply.git; cd autoapply
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1        # add -UseEdge to skip the Chromium download
```

### Manual

1. **Execution policy.** Windows blocks unsigned scripts by default. Either run each script with
   `powershell -ExecutionPolicy Bypass -File …` (affects only that process), or allow local scripts
   for your account: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
2. **Tools.**
   ```powershell
   winget install --id Git.Git -e
   winget install --id astral-sh.uv -e
   ```
   Open a new terminal so `uv` is on PATH.
3. **Python environment.** `uv python install 3.12; uv sync`
4. **Browser.** `uv run playwright install chromium` — the Windows build bundles its dependencies.
   Or use Edge: `browser.channel: msedge`.
5. **Config.** `uv run autoapply init`, edit, add `data\private\resume.pdf`, `uv run autoapply doctor`.

### Windows notes

- Paths in config can use forward slashes; `pathlib` resolves them on both OSes, and `~` expands.
- Use Windows Terminal for the Textual UI (classic conhost renders it poorly).
- Toasts go to the notification center; turn off Focus Assist / Do Not Disturb while running.
- Failure screenshots are named without `:` (illegal in Windows filenames).

## First run checklist

1. `uv run autoapply profile build` — read the summary; fix anything missing in `profile_data.yaml`.
2. `uv run autoapply jobs add <a real Greenhouse or Lever posting URL> --company X --role Y`
3. `uv run autoapply run --dry-run -n 1` — watch it fill, check every value, press Enter.
4. Sign in to any ATS account it opens (once — the persistent profile remembers).
5. Real run, headed, with `require_submit_confirmation: true`.
