#!/usr/bin/bash
# Remove only session files carrying the Jolven ownership marker
# (legacy Cartridges markers are also accepted).
set -euo pipefail

prefix="${PREFIX:-/usr}"
helper="$prefix/libexec/jolven-game-mode-setup"
legacy_helper="$prefix/libexec/cartridges-game-mode-setup"
if [[ -x "$helper" ]]; then
    sudo "$helper" uninstall
elif [[ -x "$legacy_helper" ]]; then
    sudo "$legacy_helper" uninstall
fi
targets=(
    "$prefix/bin/jolven-session"
    "$prefix/bin/cartridges-session"
    "$helper"
    "$legacy_helper"
    "$prefix/share/polkit-1/actions/io.github.zonaro.Jolven.GameMode.policy"
    "$prefix/share/polkit-1/actions/page.redclaw.Cartridges.GameMode.policy"
)
for target in "${targets[@]}"; do
    if [[ -e "$target" ]] && grep -Eq 'X-(Jolven|Cartridges)-Managed=true' "$target"; then
        sudo rm -- "$target"
        printf 'Removed %s\n' "$target"
    elif [[ -e "$target" ]]; then
        printf 'Kept unmanaged file %s\n' "$target"
    fi
done
