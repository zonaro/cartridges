<div align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.zonaro.Jolven.svg" width="128" height="128">

  # Jolven

  **Turn any Linux PC into a console.**

  A gamepad-first gaming interface for Linux. Launch your entire library from one place — use Jolven from your desktop or start directly into a dedicated gaming session.

  [![Please do not theme this app](https://stopthemingmy.app/badge.svg)](https://stopthemingmy.app)

  <img src="data/screenshots/1.png">
</div>

Explore the multilingual product page in [docs/](https://zonaro.github.io/jolven).

> Jolven is an independent fork originally based on [Cartridges](https://github.com/kra-mo/cartridges) (GPL-3.0-or-later, © 2022-2024 kramo). The origin is preserved in copyright headers, credits and the in-app About dialog.

# The Project

Jolven gathers your games from Steam, Heroic, Lutris, Bottles, emulators, native Linux games and manually added titles into one console-style library. It runs two ways:

- **Desktop mode** — Jolven as a regular app inside GNOME, KDE or another graphical environment.
- **Jolven Session** — a dedicated graphical session started straight from the display manager, fullscreen and gamepad-driven, before GNOME or KDE even load. Leaving the session returns you to the login screen.

Jolven is **not** a Linux distribution, **not** an operating system and **not** a desktop environment. It runs on top of the Linux you already have:

> Install Jolven on your existing Linux distribution and turn your PC into a console whenever you want.

## Why Jolven?

> Linux already has excellent gaming infrastructure. Jolven focuses on the missing piece: a unified, controller-first experience that can make a regular Linux PC feel like a console without replacing the operating system.

## Features

### One library for everything you play

Manually add and edit games, or let Jolven pull them in automatically from:

- Steam
- Lutris
- Heroic (Epic, GOG, Amazon, sideloaded)
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

Filter by source, search, sort by title, date added or last played, and hide the games you don't want on the couch.

### A library that looks amazing with zero effort

You press play — Jolven takes care of the shelf. No hunting for images, no blank tiles:

- **Covers that find themselves** — automatic artwork from [SteamGridDB](https://www.steamgriddb.com/), with a visual picker to choose your favorite in one click.
- **Animated covers** — your shelf comes alive with motion covers for the games you love.
- **Logos, not just titles** — clean game logos on tiles and details, so the library feels like a real console.
- **Details that help you choose** — synopsis, genres, players, co-op, age rating, release date and platform, powered by [TheGamesDB](https://thegamesdb.net/).
- **Screenshots that set the mood** — real in-game captures via [IGDB](https://www.igdb.com/) in full HD, used as the backdrop of your library.
- **Wallpapers that match your taste** — automatic and hand-picked backgrounds from [Wallhaven](https://wallhaven.cc/), with filters for category, color and mood.
- **Fallbacks that never leave you hanging** — if one source doesn't know your game, Jolven tries the next one automatically (IGDB → TheGamesDB → Wallhaven). Your library always looks finished.

> Bring your own free API keys in Preferences and unlock the full magic. Without keys, Jolven still works — with keys, it shines.

### A room that plays along with you ✨

Jolven talks to your **Tuya / Smart Life LED strips** over your local network:

- The room glows in Jolven purple when you open the app.
- When a game starts, the lights dress up in that game's color — pulled straight from its cover.
- When you quit, everything returns to exactly how it was — even if the lights were off.

One-time setup with a friendly wizard, credentials stored encrypted, and from then on it's all local and automatic. Movie night energy, every game night.

### Fast where it matters, quiet where it doesn't

- **Playtime and session tracking** — see how long you've actually played each game, automatically.
- **Per-game launch profiles** — set resolution, FPS limit, scaling, working folder and extra options once per title, then just press play.
- **GameMode + MangoHud + Gamescope, without the terminal** — toggle performance boosts and overlays per game or globally, from a normal settings screen.
- **Xbox Cloud Gaming (experimental)** — native player (no browser): sign in with your Microsoft account and stream from the cloud alongside your installed games.
- **Sunshine streaming** — export games to Sunshine with one click or automatically, then play them over Moonlight on any screen.
- **Waydroid apps** — import installed Android games with their package ids and local icons, while filtering system utilities.
- **GNOME search provider** — hit Super, type a game name, press Enter.
- **Animated, gamepad-first interface** — every screen works from the couch, with hotplug controllers, an on-screen keyboard and a global menu (library, power, session).

### Xbox Cloud Gaming (experimental)

Jolven plays Xbox Cloud Gaming with a **native player** — no browser involved:

- Sign in with your Microsoft account using a **device code** (`microsoft.com/devicelogin` on your phone or PC — your password is never typed into the app). An embedded-browser login is also available.
- Cloud streaming needs **Game Pass Ultimate**; streaming from your own console at home (xHome) works too.
- Missing GStreamer streaming plugins (ICE/libnice) are **installed by the app itself** with a system password prompt, followed by an automatic restart. System installs (`/usr`) also register the required polkit policy; fresh installs via `install.sh` already include the plugins.
- Troubleshooting: launch a game and watch the log under `~/.cache/jolven/logs/` — every page load prints an `xCloud:` diagnostics line (browser signals, WebRTC/MediaSource/codec support, script status).

> Streaming uses an unofficial client implementation inspired by [Greenlight](https://github.com/unknownskl/greenlight) (GPL-3.0). Microsoft may change the service and break it; reports with logs are welcome.

### Streaming via Sunshine (Moonlight)

Jolven exports your games to [Sunshine](https://github.com/LizardByte/Sunshine) (self-hosted game stream host for Moonlight):

- Open a game's menu and choose **Add to Sunshine**, or turn on the automatic sync in Preferences to export new games as they arrive (Xbox Cloud Gaming titles are skipped).
- The export writes to your local Sunshine `apps.json` — no account, no network — preserving anything already there.
- Then open [Moonlight](https://moonlight-stream.org/) on your TV, phone or another PC and play.

Your existing data is safe: on first launch, Jolven copies the library, covers, settings, favorites and metadata from the previous `cartridges` folders into its own folders. The originals are never deleted. GSettings keys are migrated the same way (new keys win when already customized).

# Installation

Jolven roda apenas em Linux. Baixe o binário da última
[GitHub Release](https://github.com/zonaro/jolven/releases) ou compile do
código-fonte (veja [CONTRIBUTING.md](CONTRIBUTING.md#building)).

On Fedora, install the latest binary release for your user with:

```sh
curl -fsSL https://raw.githubusercontent.com/zonaro/jolven/main/install.sh | bash
```

The installer downloads the prebuilt binary from the latest GitHub Release,
installs any missing runtime dependencies and extracts Jolven under
`~/.local`. To compile from source instead, pipe into `bash -s -- --source`.
Each build is versioned `YY.DDD.HHMM` (2-digit year, day of year, 24h
hour+minute of the compilation) — see `scripts/publish-release.sh`.

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
audio input when multiple devices are available. It also selects whether the
in-game overlay opens with Button Mode, Button Home, or either one. Default
settings preserve the existing desktop launch behavior. Each game's details dialog has a
persisted launch profile for global GameMode/MangoHud overrides, working
directory, JSON environment, Gamescope options, FPS limit, resolution,
scaling and an optional process name for launcher hand-offs.

Gamepad navigation uses libmanette and supports hotplug; D-pad/left stick
navigate, the south face button confirms, the east face button goes back,
Start opens the page menu, and Guide/Home opens the global menu. That menu can
return to the library, close the current tracked game, suspend, restart, shut
down or leave the Jolven Session. Power actions use systemd-logind over D-Bus
and do not call sudo.

While a tracked game is running, the configured Mode/Home button brings the
Jolven overlay to the front. From there users can return to the game, add a
session note, finish tracking, or open an installed
[GPU Screen Recorder](https://flathub.org/apps/com.dec05eba.gpu_screen_recorder)
from the recording icon. The integration supports its registered desktop app
(including Flatpak) and native GTK/UI executables; GPU Screen Recorder remains
optional.

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

- Original project: [Cartridges](https://github.com/kra-mo/cartridges) by kramo and contributors (GPL-3.0-or-later).
- This fork: Jolven contributors — rebranded as an independent, console-focused gaming interface with a dedicated session, automatic data migration and a new identity.
