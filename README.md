[circle-url]: https://circle.gnome.org
[circle-image]: https://circle.gnome.org/assets/button/badge.svg
[donttheme-url]: https://stopthemingmy.app
[donttheme-image]: https://stopthemingmy.app/badge.svg
[weblate-url]: https://hosted.weblate.org/engage/cartridges/
[weblate-image]: https://hosted.weblate.org/widgets/cartridges/-/cartridges/svg-badge.svg
[discord-url]: https://discord.gg/yrJfddyt56
[discord-image]: https://img.shields.io/discord/1088155799299313754?color=%235865F2&label=discord&logo=discord&logoColor=%23FFFFFF&style=for-the-badge
[flathub-url]: https://flathub.org/apps/page.kramo.Cartridges
[flathub-image]: https://img.shields.io/flathub/v/page.kramo.Cartridges?logo=flathub&style=for-the-badge
[installs-image]: https://img.shields.io/flathub/downloads/page.kramo.Cartridges?style=for-the-badge

> [!IMPORTANT]
> This project is no longer actively maintained.
> The `rewrite` branch has the most up-to-date code in case someone wants to keep it alive through a fork, but be aware that not everything there is functional. Still, please don't use code from the `main` branch as it's of poor quality.

<div align="center">
  <img src="data/icons/hicolor/scalable/apps/page.kramo.Cartridges.svg" width="128" height="128">

  # Cartridges

  A GTK4 + Libadwaita game launcher

  [![GNOME Circle][circle-image]][circle-url]
  [![Please do not theme this app][donttheme-image]][donttheme-url] 
  [![Translation Status][weblate-image]][weblate-url]

  [![Flathub][flathub-image]][flathub-url]
  [![Discord][discord-image]][discord-url]
  [![Installs][installs-image]][flathub-url]

  <img src="data/screenshots/1.png">
</div>

Explore the multilingual product page in [landing/](landing/index.html).

# The Project

Cartridges is an easy-to-use, elegant game launcher written in Python using GTK4 and Libadwaita.

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
  - Flatpak
  - Desktop Entries
- Filtering games by source
- Searching and sorting by title, date added and last played
- Hiding games
- Automatically downloading cover art from [SteamGridDB](https://www.steamgriddb.com/)
- Game details, screenshots, and fallback artwork from [TheGamesDB](https://thegamesdb.net/)
- Searching for games on various databases
- Animated covers
- A search provider for GNOME

For updates and questions, join our [Discord server][discord-url]!

## Donations
I accept donations through [GitHub Sponsors](https://github.com/sponsors/kra-mo) and [Liberapay](https://liberapay.com/kramo).

Thank you for your generosity! 💜

# Installation

## Linux

On Fedora, install the latest binary release for your user with:

```sh
curl -fsSL https://raw.githubusercontent.com/zonaro/cartridges/main/install.sh | bash
```

The installer downloads the prebuilt binary from the latest GitHub Release,
installs any missing runtime dependencies and extracts Cartridges under
`~/.local`. To compile from source instead, pipe into `bash -s -- --source`.
Each build is versioned `YY.DDD.HHMM` (2-digit year, day of year, 24h
hour+minute of the compilation) — see `scripts/publish-release.sh`.

The original app is also available on Flathub.

<a href=https://flathub.org/apps/page.kramo.Cartridges><img alt='Download on Flathub' src='https://flathub.org/api/badge?svg&locale=en'/></a>

## Windows

### From Releases

1. Download the latest release from [GitHub Releases](https://github.com/kra-mo/cartridges/releases).
2. Run the downloaded installer.

Note: Windows might present you with a warning when trying to install the app. This is expected, just ignore the warning.

### Winget

Install the latest release with the command: `winget install cartridges`.

## macOS

1. Download the latest release from [GitHub Releases](https://github.com/kra-mo/cartridges/releases).
2. Move the app into your Applications folder.

Note: macOS might tell you that the application could not be checked for malicious software or something similar. In this case, open System Settings > Privacy & Security, scroll down, find the warning about Cartridges and click "Open Anyway". More information can be found [here](https://support.apple.com/en-us/102445).

## Building manually

See [Building](https://codeberg.org/kramo/cartridges/src/branch/main/CONTRIBUTING.md#building).

## Game Mode

Cartridges can also be the frontend of a standalone Gamescope session. This
does not start GNOME Shell and does not change normal desktop launches. The
same application and library are used in both modes.

### Requirements and installation (Fedora)

Build and install Cartridges normally, then run:

```sh
./scripts/install-game-session.sh
```

The installer verifies Fedora, checks `gamescope`, `gamemode`, and
`libmanette`, installs only missing packages with DNF, validates the session
desktop file, and installs the session launcher under `/usr`. It never
removes dependencies or changes GPU, SELinux, PAM, or GDM settings. MangoHud
is optional.

The application menu also exposes **Instalar Gaming Mode** or **Desinstalar
Gaming Mode**, according to the current state. It uses the normal polkit
authorization dialog. The login-manager entry uses the distribution name
from `/etc/os-release`, for example **Fedora Gaming Mode**.

Log out, select **<Distribution> Gaming Mode** (for example, **Fedora Gaming
Mode**) from the display manager's session chooser, and sign in. Closing
Cartridges ends Gamescope and returns to the login screen. To remove only the
session integration:

```sh
./scripts/uninstall-game-session.sh
```

### Development and configuration

Test the console UI without logging out:

```sh
cartridges --game-mode --windowed
```

Game Mode starts on a console-style landing page showing the current Linux
account name and AccountsService (or `~/.face`) avatar, with Continue,
Library, Settings and Power actions. **Start in Library** can bypass that page.

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
down or leave Game Mode. Power actions use systemd-logind over D-Bus and do
not call sudo.

### Troubleshooting

Session launcher messages are available with:

```sh
journalctl -t cartridges-session -b
journalctl --user -b | grep -i cartridges
```

Check `gamescope --version`, `gamemoded -t`, and the app log under
`~/.cache/cartridges/logs/` when a component fails. The session retries once
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

See [CONTRIBUTING.md](https://codeberg.org/kramo/cartridges/src/branch/main/CONTRIBUTING.md).

Thanks to [Weblate](https://weblate.org/) for hosting our translations!

# Code of Conduct

The project follows the [GNOME Code of Conduct](https://conduct.gnome.org/). 

Cartridges' contributors stand with Palestine, Ukraine and all other victims of imperialism and war, and believe trans rights are human rights. If this bothers you, this is probably not the best project for you to contribute to.

See [CODE_OF_CONDUCT.md](https://codeberg.org/kramo/cartridges/src/branch/main/CODE_OF_CONDUCT.md).
