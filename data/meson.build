blueprint_compiler = find_program('blueprint-compiler')
blueprints = []
foreach name : [
  'details-dialog',
  'fita-wizard',
  'game',
  'logo-picker',
  'preferences',
  'sgdb-picker',
  'shortcuts-dialog',
  'wallpaper-picker',
  'window',
]
  blueprints += custom_target(
    name + '-blueprint',
    input: 'gtk/' + name + '.blp',
    output: name + '.ui',
    command: [blueprint_compiler, 'compile', '--output', '@OUTPUT@', '@INPUT@'],
  )
endforeach

desktop_file = i18n.merge_file(
  input: configure_file(
    input: 'io.github.zonaro.Jolven.desktop.in',
    output: app_id + '.desktop.in',
    configuration: conf,
  ),
  output: app_id + '.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

appstream_file = i18n.merge_file(
  input: configure_file(
    input: 'io.github.zonaro.Jolven.metainfo.xml.in',
    output: app_id + '.metainfo.xml.in',
    configuration: conf,
  ),
  output: app_id + '.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

gnome.compile_resources(
  'jolven',
  configure_file(
    input: 'jolven.gresource.xml.in',
    output: 'cartridges.gresource.xml',
    configuration: conf,
  ),
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
  dependencies: [blueprints, appstream_file],
)

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test(
    'Validate appstream file',
    appstreamcli,
    args: ['validate', '--no-net', '--explain', appstream_file],
    workdir: meson.current_build_dir(),
  )
endif

schema_file = configure_file(
  input: 'io.github.zonaro.Jolven.gschema.xml.in',
  output: app_id + '.gschema.xml',
  configuration: conf,
)

install_data(
  schema_file,
  install_dir: join_paths(get_option('datadir'), 'glib-2.0/schemas'),
)

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test(
    'Validate schema file',
    compile_schemas,
    args: ['--strict', '--dry-run', meson.current_build_dir()],
  )
endif

subdir('icons')

if get_option('prefix') in ['/usr', '/usr/local']
  configure_file(
    input: 'io.github.zonaro.Jolven.GameMode.policy.in',
    output: app_id + '.GameMode.policy',
    configuration: conf,
    install: true,
    # Polkit uses a system integration directory. User-local builds leave
    # this file to the dedicated Game Mode installer instead of requiring
    # root for an otherwise user-local application update.
    install_dir: '/usr/share/polkit-1/actions',
  )
endif
