configure_file(
  input: 'jolven.in',
  output: 'jolven',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
)

install_subdir('importer', install_dir: moduledir)
install_subdir('utils', install_dir: moduledir)
install_subdir('store', install_dir: moduledir)
install_subdir('logging', install_dir: moduledir)
install_subdir('errors', install_dir: moduledir)
install_subdir('xcloud_native', install_dir: moduledir)
install_subdir('recorder', install_dir: moduledir)

# Versão de compilação (Fixes #6): regenerada a cada `meson compile` com a
# data atual (YY.DDD.HHMM via build-aux/write-version.py). Tem precedência
# sobre o VERSION congelado no `meson setup` (ver shared.py.in).
# custom_target com build_always_stale funciona desde meson 0.59.
version_py = custom_target(
  'jolven-version',
  input: 'version.py.in',
  output: 'version.py',
  command: [
    py_installation.full_path(),
    meson.project_source_root() / 'build-aux' / 'write-version.py',
    '@INPUT@',
    '@OUTPUT@',
  ],
  build_always_stale: true,
  install: true,
  install_dir: moduledir,
)
install_data(
  [
    'main.py',
    'window.py',
    'preferences.py',
    'details_dialog.py',
    'game.py',
    'game_cover.py',
    'onscreen_keyboard.py',
    'keyboard_emulator.py',
    'logo_picker.py',
    'process_session.py',
    'botao_tarefas.py',
    'tarefas_janela.py',
    'fita_wizard.py',
    'wallpaper_picker.py',
    'zerados_picker.py',
    'session_history.py',
    'runtime.py',
    'game_launch.py',
    'power.py',
    'user_profile.py',
    'hardware.py',
    'controller.py',
    'gamepad_visual.py',
    'launchers.py',
    'xcloud_catalog.py',
    'xcloud_deps.py',
    'xcloud_login.py',
    'xcloud_player.py',
    'image_picker.py',
    'wallhaven_filter_bar.py',
    configure_file(input: 'shared.py.in', output: 'shared.py', configuration: conf),
  ],
  install_dir: moduledir,
)

if host_machine.system() == 'linux'
  configure_file(
    input: '../session/jolven-session.in',
    output: 'jolven-session',
    configuration: conf,
    install: true,
    install_mode: 'rwxr-xr-x',
    install_dir: get_option('bindir'),
  )
  configure_file(
    input: '../session/jolven-game-mode-setup.in',
    output: 'jolven-game-mode-setup',
    configuration: conf,
    install: true,
    install_mode: 'rwxr-xr-x',
    install_dir: get_option('libexecdir'),
  )
  configure_file(
    input: '../session/jolven-xcloud-deps.in',
    output: 'jolven-xcloud-deps',
    configuration: conf,
    install: true,
    install_mode: 'rwxr-xr-x',
    install_dir: get_option('libexecdir'),
  )
endif
