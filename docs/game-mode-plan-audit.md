# Game Mode plan audit

This matrix maps every implementation requirement in
`plano-gamemode.prompt.md` to the delivered code and its validation.

| Plan | Coverage | Evidence |
|---|---|---|
| 1. Desktop mode | Implemented | Desktop is the default runtime; no Gamescope, fullscreen, power shell or session side effects are enabled. An isolated desktop smoke test verifies a normal non-fullscreen window. |
| 2. Game session mode | Implemented | Dedicated runtime is fullscreen, console-oriented and exits with its frontend. |
| 3. Gamescope | Implemented | `cartridges-session` starts Gamescope directly, with verified Fedora 44 flags and safe fallback for optional monitor/VRR/FPS options. HDR remains deliberately opt-in/not enabled. |
| 4. Feral GameMode | Implemented | Per-game/global opt-in uses `gamemoderun` only when available; missing GameMode falls back safely. |
| 5. Dependencies | Implemented | Fedora helper checks RPMs before DNF installs `gamescope`, `gamemode`, `libmanette`, `desktop-file-utils`, `polkit` and `wireplumber`; MangoHud is optional. |
| 6. Login session | Implemented | The helper generates a standard `/usr/share/wayland-sessions` entry named from `/etc/os-release`, such as `Fedora Gaming Mode`. |
| 7. Session entrypoint | Implemented | Environment, privilege checks, process-group signal forwarding, audio selection/restoration, retry, cleanup and exit status are handled by `cartridges-session`. |
| 8. Game lifecycle | Implemented | `ProcessSession` follows process groups, Steam ids, Flatpak ids, executable names and install trees; explicit per-game process tracking covers detached launchers. |
| 9. Focus | Implemented | The game gets compositor focus when mapped; after tracked exit Cartridges calls `present()` and restores its console UI. No fixed focus sleep is used. |
| 10. Gamepad | Implemented | libmanette hotplug, semantic buttons, D-pad/stick GTK focus, confirm/back/context, dialogs and an on-screen search keyboard cover controller-only operation. |
| 11. Guide/Home | Implemented | Guide opens the global Game Mode menu through the controller abstraction; no direct `/dev/input` access is used. |
| 12. Power menu | Implemented | Library, close game, suspend, restart, shutdown and logout use GTK plus systemd-logind D-Bus, never `sudo`. |
| 13. Per-game settings | Implemented | Persisted GameMode/MangoHud overrides, working directory, environment, Gamescope options, FPS, resolution, scaling and tracked executable are editable in game details. |
| 14. Game Mode settings | Implemented | Global toggles plus DRM monitor and PipeWire input/output choices; unsupported VRR and unavailable integrations are hidden or disabled. |
| 15. MangoHud | Implemented | Optional detection and safe wrapper fallback; it is not installed as a dependency. |
| 16. Steam | Implemented | Native Steam is preferred, Flatpak is a fallback, `-silent` avoids Big Picture, and Steam environment ids track the real game. |
| 17. Other launchers/emulators | Implemented | Existing imported commands are preserved without fixed paths; process-name profiles support Heroic, Lutris, Wine, Proton, emulators and scripts. |
| 18. Flatpak | Implemented | Host command discovery/launch uses `flatpak-spawn`; Steam and game tracking understand Flatpak ids. Host-session installation is hidden inside a Flatpak sandbox. |
| 19. Logs/debug | Implemented | The session logs to the journal; app/game lifecycle failures use existing logs and high-priority toasts. |
| 20. Development mode | Implemented | `cartridges --game-mode --windowed` and `--nested` use a non-unique nested runtime without claiming the login session. |
| 21. Installer | Implemented | Shell install/uninstall scripts and the menu/polkit path install only owned integration and retain dependencies on removal. |
| 22. Privileges | Implemented | Root is limited to package/session-file setup; the graphical session refuses root. |
| 23. Security | Implemented | No PAM, SELinux, udev, GPU-global or broad-permission changes; unmanaged files are backed up/refused during removal. |
| 24. Failure recovery | Implemented | Early Gamescope failure retries safe defaults; frontend failure ends Gamescope; game failure restores the UI; session shutdown escalates only attributed game processes. |
| 25. Runtime detection | Implemented | `RuntimeContext` centralizes desktop, dedicated and nested modes. |
| 26. Architecture | Implemented | Runtime, command composition, launcher resolution, hardware, controller, power, user profile and process lifecycle are separate modules aligned with the existing project. |
| 27. Shared UI | Implemented | Landing, library and details use one application, store and navigation hierarchy; no duplicate launcher/library was created. |
| 28. Console UX | Implemented | Large controls, strong focus ring, landing page, initial focus, fullscreen session, controller keyboard and focused errors preserve the Cartridges visual identity. |
| 29. Automated tests | Implemented | Runtime positive/negative cases, commands, wrappers, Steam, Flatpak, process lifecycle, controller parsing, hardware, profile, configuration and helper name are covered. |
| 30. Validation | Implemented where non-destructive | Clean build, 30 unit tests, schema/AppStream/desktop validation, staged install, helper install/uninstall in a user namespace, desktop smoke test, nested Game Mode UI and a real nested Gamescope compositor run pass. A physical GDM login requires logging out and remains the final operator acceptance test. |
| 31. Compatibility | Implemented | Fedora/GDM/Wayland are primary; the standard Wayland session file and isolated platform modules leave room for KDE display managers and other distributions. |
| 32. Documentation | Implemented | README covers requirements, install/remove, login selection, nested mode, profiles, troubleshooting, logs and limitations; architecture has a separate document. |
| 33. Preserve existing app | Validated | Desktop smoke test, shared store/UI, existing import pipeline, build and full test suite remain functional. |
| 34–36. Delivery flow/outcome | Completed | The phases are represented by the modules and validations above; small technical choices were resolved in favor of safe desktop compatibility. |
| 37. Final report | Completed in handoff | The final delivery report lists architecture, files, dependencies, modes, exact commands, tests and limitations. |

## Hardware/operator acceptance

Two checks cannot be automated without disrupting the current user session or
having the device attached:

1. log out, select the generated distribution-named session in GDM, launch and
   close a real game, then exit Cartridges and confirm the return to GDM;
2. repeat the essential navigation path with the intended physical controller,
   including its Guide/Home button, because firmware and driver mappings vary.

These are acceptance checks for the implemented paths, not missing code paths.
