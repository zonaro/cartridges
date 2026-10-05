# Heavily inspired by https://gitlab.gnome.org/World/lollypop/-/blob/master/search-provider/meson.build

service_dir = join_paths(get_option('datadir'), 'dbus-1', 'services')
serarch_provider_dir = join_paths(get_option('datadir'), 'gnome-shell', 'search-providers')

configure_file(
  input: 'jolven-search-provider.in',
  output: 'jolven-search-provider',
  configuration: conf,
  install_dir: libexecdir,
)

configure_file(
  input: 'io.github.zonaro.Jolven.SearchProvider.service.in',
  output: app_id + '.SearchProvider.service',
  configuration: conf,
  install_dir: service_dir,
)

configure_file(
  input: 'io.github.zonaro.Jolven.SearchProvider.ini',
  output: app_id + '.SearchProvider.ini',
  configuration: conf,
  install_dir: serarch_provider_dir,
)
