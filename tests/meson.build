test(
  'Python unit tests',
  py_installation,
  args: ['-m', 'unittest', 'discover', '-s', meson.current_source_dir(), '-v'],
  env: ['PYTHONPATH=' + meson.project_source_root()],
)
