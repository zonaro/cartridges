scalable_dir = join_paths('hicolor', 'scalable', 'apps')
install_data(
  join_paths(scalable_dir, ('@0@.svg').format(app_id)),
  install_dir: join_paths(get_option('datadir'), 'icons', scalable_dir),
)

symbolic_dir = join_paths('hicolor', 'symbolic', 'apps')
install_data(
  join_paths(symbolic_dir, ('@0@-symbolic.svg').format(app_id)),
  install_dir: join_paths(get_option('datadir'), 'icons', symbolic_dir),
)

# PNGs pre-renderizados para ambientes sem carregador SVG.
foreach size : ['32x32', '64x64', '128x128', '256x256', '512x512']
  png_dir = join_paths('hicolor', size, 'apps')
  install_data(
    join_paths(png_dir, ('@0@.png').format(app_id)),
    install_dir: join_paths(get_option('datadir'), 'icons', png_dir),
  )
endforeach
