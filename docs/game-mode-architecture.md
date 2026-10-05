# Game Mode architecture

> In Jolven, the user-facing name of this experience is **Jolven Session**.
> Internally the code still uses the `game-mode`/`game-session` terms for
> flags, schemas and helpers (`jolven --game-mode`, `jolven-session`).

## Runtime and shared UI

`runtime.py` is the only authority for desktop, dedicated-session, and nested
mode detection. UI code consumes its immutable `RuntimeContext`; it does not
scatter environment checks through the application. Desktop remains the
default. The console landing page, Linux account name/avatar, large actions,
global menu and fullscreen presentation are enabled only in a game runtime.
The landing page and the normal library live in the same navigation view, so
there is no second application or duplicated library implementation.

## Login session and installer

Meson installs `jolven-session`, a fixed-operation privileged helper and a
polkit policy. The helper generates the display-manager entry under
`share/wayland-sessions` at installation time; its visible name is
`Jolven Session`.
Only the small helper runs as root. Jolven, Gamescope and games explicitly
refuse or avoid root execution.

The GDM entry starts `jolven-session`. That launcher validates its
privilege level and dependencies, selects optional monitor/VRR/FPS/audio
settings, starts Gamescope in a separate process group, forwards termination
signals, and waits for the compositor. Jolven is Gamescope's primary
child, so closing or crashing it ends Gamescope and the login session.
Optional Gamescope options get one safe-default retry when startup fails
immediately. Audio defaults are restored during cleanup.

The UI invokes the helper through `pkexec`; the shell installer offers the
same operation for development or packaging. Uninstall checks an ownership
marker and never removes Gamescope, GameMode, libmanette or user data.

## Games and launch profiles

Game launching remains shared between modes. `game_launch.py` safely composes
optional `gamemoderun`, MangoHud and per-game Gamescope wrappers. Gamescope is
never nested around a game in the dedicated Gamescope session. The persisted
profile on every game can override GameMode and MangoHud and store working
directory, environment, Gamescope options, FPS limit, resolution, scaling,
and an explicit process executable for launchers that hand off.

`ProcessSession` follows the dedicated process group, Steam app environment,
Flatpak app id, executable or install tree instead of trusting only the
launcher's initial PID. When the process family disappears, the shared window
is presented to request focus from Gamescope. Closing the dedicated session
sends TERM, waits briefly, then kills only still-attributed game processes;
the final playtime is persisted before shutdown.

Steam is resolved as a native or Flatpak backend and starts silently. Other
launchers keep their imported commands; explicit process tracking covers
Heroic, Lutris, Wine, Proton, emulator and script hand-offs without hardcoded
installation paths.

## Input, power and hardware

Gamepad input stays at libmanette's high-level controller abstraction,
including hotplug; the application never opens `/dev/input`. Semantic south,
east, Start and Guide actions map to confirm, back, context and global menu,
while D-pad/left stick move GTK focus. Dialogs, details, preferences and error
actions use the same GTK focus graph.

Power operations go through systemd-logind's D-Bus API. Hardware discovery is
read-only: DRM exposes connected monitor/VRR capabilities and `wpctl` exposes
PipeWire input/output nodes. Unsupported controls are hidden or disabled;
there are no global GPU changes.
