project(
  'cartridges',
  # Versionamento por data da compilacao: YY.DDD.HHMM (ano 2 digitos,
  # dia do ano, hora+minuto 24h). Calculado por build-aux/get-version.py
  # a cada `meson setup`; defina CARTRIDGES_VERSION para congelar uma versao.
  version: run_command(
    'python3',
    files('build-aux/get-version.py'),
    check: true,
  ).stdout().strip(),
  meson_version: '>= 0.59.0',
  default_options: [
    'warning_level=2',
    'werror=false',
  ],
)

dependency('gtk4', version: '>= 4.15.0')
dependency('libadwaita-1', version: '>= 1.6.beta')

# Translations are broken on Windows for multiple reasons
# gresources don't work and MSYS2 seems to have also broken the gettext package
if host_machine.system() != 'windows'
  i18n = import('i18n')
endif

gnome = import('gnome')
python = import('python')

py_installation = python.find_installation('python3')

python_dir = join_paths(get_option('prefix'), py_installation.get_install_dir())
pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
libexecdir = join_paths(get_option('prefix'), get_option('libexecdir'))
moduledir = python_dir / 'cartridges'

profile = get_option('profile')
if profile == 'development'
  app_id = 'page.kramo.Cartridges.Devel'
  prefix = '/page/kramo/Cartridges/Devel'
elif profile == 'release'
  app_id = 'page.kramo.Cartridges'
  prefix = '/page/kramo/Cartridges'
endif

conf = configuration_data()
conf.set('PYTHON', py_installation.full_path())
conf.set('PYTHON_VERSION', py_installation.language_version())
conf.set('APP_ID', app_id)
conf.set('PREFIX', prefix)
conf.set('VERSION', meson.project_version())
conf.set('PROFILE', profile)
conf.set('TIFF_COMPRESSION', get_option('tiff_compression'))
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)
conf.set('pythondir', python_dir)
conf.set('libexecdir', libexecdir)
conf.set('bindir', join_paths(get_option('prefix'), get_option('bindir')))
conf.set('datadir', join_paths(get_option('prefix'), get_option('datadir')))
if host_machine.system() == 'linux'
  conf.set('game_session_datadir', '/usr/share')
else
  conf.set(
    'game_session_datadir',
    join_paths(get_option('prefix'), get_option('datadir')),
  )
endif

subdir('data')
subdir('cartridges')
subdir('tests')

if host_machine.system() == 'windows'
  subdir('build-aux/windows')
else
  subdir('search-provider')
  subdir('po')
endif

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
