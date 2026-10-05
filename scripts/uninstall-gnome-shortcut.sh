#!/usr/bin/env bash
# Remove o atalho global Super+G do Jolven no GNOME.
set -Eeuo pipefail

readonly SHORTCUT_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jolven/"
readonly LEGACY_SHORTCUT_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/cartridges/"
readonly MEDIA_KEYS_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
readonly CUSTOM_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

if ! command -v gsettings >/dev/null 2>&1; then
    printf 'gsettings nao encontrado; nada a remover.\n' >&2
    exit 1
fi

current_list="$(gsettings get "$MEDIA_KEYS_SCHEMA" custom-keybindings)"
for path in "$SHORTCUT_PATH" "$LEGACY_SHORTCUT_PATH"; do
    if [[ "$current_list" == *"$path"* ]]; then
        current_list="$(printf '%s' "$current_list" \
            | sed -e "s#, '$path'##" -e "s#'$path', *##" -e "s#'$path'##")"
    fi
done
[[ "$current_list" == "["*"]" ]] || current_list="[]"
current_list="$(printf '%s' "$current_list" | sed -e 's/\[, /[/' -e 's/\[,/\[/' -e 's/\[ *\]/[]/')"
gsettings set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$current_list"

gsettings reset-recursively "$CUSTOM_SCHEMA:$SHORTCUT_PATH" 2>/dev/null || true
gsettings reset-recursively "$CUSTOM_SCHEMA:$LEGACY_SHORTCUT_PATH" 2>/dev/null || true

printf 'Atalho global do Jolven removido.\n'
