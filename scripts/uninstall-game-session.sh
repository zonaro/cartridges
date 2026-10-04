#!/usr/bin/bash
# Remove only session files carrying the Cartridges ownership marker.
set -euo pipefail

prefix="${PREFIX:-/usr}"
helper="$prefix/libexec/cartridges-game-mode-setup"
if [[ -x "$helper" ]]; then
    sudo "$helper" uninstall
fi
targets=(
    "$prefix/bin/cartridges-session"
    "$helper"
    "$prefix/share/polkit-1/actions/page.kramo.Cartridges.GameMode.policy"
)
for target in "${targets[@]}"; do
    if [[ -e "$target" ]] && grep -q 'X-Cartridges-Managed=true' "$target"; then
        sudo rm -- "$target"
        printf 'Removed %s\n' "$target"
    elif [[ -e "$target" ]]; then
        printf 'Kept unmanaged file %s\n' "$target"
    fi
done
