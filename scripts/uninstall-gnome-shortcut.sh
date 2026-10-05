#!/usr/bin/env bash
# Remove o atalho global Super+G do Cartridges no GNOME.
set -Eeuo pipefail

readonly SHORTCUT_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/cartridges/"
readonly MEDIA_KEYS_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
readonly CUSTOM_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

if ! command -v gsettings >/dev/null 2>&1; then
    printf 'gsettings nao encontrado; nada a remover.\n' >&2
    exit 1
fi

current_list="$(gsettings get "$MEDIA_KEYS_SCHEMA" custom-keybindings)"
if [[ "$current_list" == *"$SHORTCUT_PATH"* ]]; then
    # Remove o path da lista preservando as demais entradas.
    new_list="$(printf '%s' "$current_list" \
        | sed -e "s#, '$SHORTCUT_PATH'##" -e "s#'$SHORTCUT_PATH', *##" -e "s#'$SHORTCUT_PATH'##")"
    [[ "$new_list" == "["*"]" ]] || new_list="[]"
    # Normaliza "[, ]" ou "[" que podem sobrar apos o sed.
    new_list="$(printf '%s' "$new_list" | sed -e 's/\[, /[/' -e 's/\[,/\[/' -e 's/\[ *\]/[]/')"
    gsettings set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$new_list"
fi

gsettings reset-recursively "$CUSTOM_SCHEMA:$SHORTCUT_PATH" 2>/dev/null || true

printf 'Atalho global do Cartridges removido.\n'
