<div align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.zonaro.Jolven.svg" width="128" height="128">

  # Jolven

  **Turn any Linux PC into a console.**

  A gamepad-first gaming interface for Linux. Launch your entire library from one place — use Jolven from your desktop or start directly into a dedicated gaming session.

  [![Please do not theme this app](https://stopthemingmy.app/badge.svg)](https://stopthemingmy.app)

  <img src="data/screenshots/1.png">
</div>

Explore the multilingual product page in [landing/](landing/index.html).

> Jolven is an independent fork originally based on [Cartridges](https://github.com/zonaro/cartridges) (GPL-3.0-or-later, © 2022-2024 redclaw). The origin is preserved in copyright headers, credits and the in-app About dialog.

# The Project

Jolven gathers your games from Steam, Heroic, Lutris, Bottles, emulators, native Linux games and manually added titles into one console-style library. It runs two ways:

- **Desktop mode** — Jolven as a regular app inside GNOME, KDE or another graphical environment.
- **Jolven Session** — a dedicated graphical session started straight from the display manager, fullscreen and gamepad-driven, before GNOME or KDE even load. Leaving the session returns you to the login screen.

Jolven is **not** a Linux distribution, **not** an operating system and **not** a desktop environment. It runs on top of the Linux you already have:

> Install Jolven on your existing Linux distribution and turn your PC into a console whenever you want.

## Why Jolven?

> Linux already has excellent gaming infrastructure. Jolven focuses on the missing piece: a unified, controller-first experience that can make a regular Linux PC feel like a console without replacing the operating system.

## Features

- Manually adding and editing games
- Importing games from various sources:
  - Steam
  - Lutris
  - Heroic
  - Bottles
  - itch
  - Legendary
  - RetroArch
  - Dolphin
  - Yuzu
  - TwinTail
  - Flatpak
  - Desktop Entries
- Xbox Cloud Gaming (experimental)
- Filtering games by source
- Searching and sorting by title, date added and last played
- Hiding games
- Automatically downloading cover art from [SteamGridDB](https://www.steamgriddb.com/)
- Game details, screenshots, and fallback artwork from [TheGamesDB](https://thegamesdb.net/)
- Searching for games on various databases
- Animated covers
- Playtime and session tracking, per-game launch profiles
- A search provider for GNOME

Your existing data is safe: on first launch, Jolven copies the library, covers, settings, favorites and metadata from the previous `cartridges` folders into its own folders. The originals are never deleted. GSettings keys are migrated the same way (new keys win when already customized).

# Installation

## Linux

On Fedora, install the latest binary release for your user with:

```sh
curl -fsSL https://raw.githubusercontent.com/zonaro/cartridges/main/install.sh | bash
```

The installer downloads the prebuilt binary from the latest GitHub Release,
installs any missing runtime dependencies and extracts Jolven under
`~/.local`. To compile from source instead, pipe into `bash -s -- --source`.
Each build is versioned `YY.DDD.HHMM` (2-digit year, day of year, 24h
hour+minute of the compilation) — see `scripts/publish-release.sh`.

## Windows

### From Releases

1. Download the latest release from [GitHub Releases](https://github.com/zonaro/cartridges/releases).
2. Run the downloaded installer.

Note: Windows might present you with a warning when trying to install the app. This is expected, just ignore the warning.

## macOS

1. Download the latest release from [GitHub Releases](https://github.com/zonaro/cartridges/releases).
2. Move the app into your Applications folder.

## Building manually

See [CONTRIBUTING.md](CONTRIBUTING.md#building).

## Jolven Session

Jolven can also be the frontend of a standalone Gamescope session. This
does not start GNOME Shell and does not change normal desktop launches. The
same application and library are used in both modes.

### Requirements and installation (Fedora)

Build and install Jolven normally, then run:

```sh
./scripts/install-game-session.sh
```

The installer verifies Fedora, checks `gamescope`, `gamemode`, and
`libmanette`, installs only missing packages with DNF, validates the session
desktop file, and installs the session launcher under `/usr`. It never
removes dependencies or changes GPU, SELinux, PAM, or GDM settings. MangoHud
is optional.

The application menu also exposes **Install Jolven Session** or **Uninstall
Jolven Session**, according to the current state. It uses the normal polkit
authorization dialog.

Log out, select **Jolven Session** from the display manager's session
chooser, and sign in. Closing Jolven ends Gamescope and returns to the login
screen. To remove only the session integration:

```sh
./scripts/uninstall-game-session.sh
```

### Development and configuration

Test the console UI without logging out:

```sh
jolven --game-mode --windowed
```

(`--jolven-session` works as an alias for `--game-mode`.)

The Session preferences page controls GameMode, optional MangoHud, Gamescope
VRR/FPS limiting, cursor hiding, and the preferred display, audio output and
audio input when multiple devices are available. Default settings preserve
the existing desktop launch behavior. Each game's details dialog has a
persisted launch profile for global GameMode/MangoHud overrides, working
directory, JSON environment, Gamescope options, FPS limit, resolution,
scaling and an optional process name for launcher hand-offs.

Gamepad navigation uses libmanette and supports hotplug; D-pad/left stick
navigate, the south face button confirms, the east face button goes back,
Start opens the page menu, and Guide/Home opens the global menu. That menu can
return to the library, close the current tracked game, suspend, restart, shut
down or leave the Jolven Session. Power actions use systemd-logind over D-Bus
and do not call sudo.

### Troubleshooting

Session launcher messages are available with:

```sh
journalctl -t jolven-session -b
journalctl --user -b | grep -i jolven
```

Check `gamescope --version`, `gamemoded -t`, and the app log under
`~/.cache/jolven/logs/` when a component fails. The session retries once
with safe Gamescope defaults if monitor, VRR, or limiter options fail during
startup.

Known limitations: HDR is deliberately not enabled automatically; Guide/Home
delivery depends on the controller and its driver; a launcher that deliberately
detaches without Steam/Flatpak identity may need its process name configured
in the game profile; Flatpak builds cannot install a host GDM session
themselves. The previous PipeWire defaults are restored when the session exits.

The implementation and responsibility boundaries are documented in
[`docs/game-mode-architecture.md`](docs/game-mode-architecture.md).

# Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

# Credits

- Original project: [Cartridges](https://github.com/zonaro/cartridges) by redclaw and contributors (GPL-3.0-or-later).
- This fork: Jolven contributors — rebranded as an independent, console-focused gaming interface with a dedicated session, automatic data migration and a new identity.
