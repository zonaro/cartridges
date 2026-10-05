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
install_data(
  [
    'main.py',
    'window.py',
    'preferences.py',
    'details_dialog.py',
    'game.py',
    'game_cover.py',
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
    'launchers.py',
    'xcloud.py',
    'sgdb_picker.py',
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
endif
