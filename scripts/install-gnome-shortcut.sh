#!/usr/bin/env bash
# Configura o atalho global Super+G para abrir o Jolven no GNOME.
# Idempotente: preserva atalhos personalizados existentes e apenas garante
# que a entrada do Jolven exista com o binding <Super>g.
set -Eeuo pipefail

readonly BINDING="${JOLVEN_SHORTCUT_BINDING:-${CARTRIDGES_SHORTCUT_BINDING:-<Super>g}}"
readonly SHORTCUT_NAME="${JOLVEN_SHORTCUT_NAME:-${CARTRIDGES_SHORTCUT_NAME:-Jolven}}"
readonly SHORTCUT_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jolven/"
readonly MEDIA_KEYS_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
readonly CUSTOM_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

resolve_command() {
    if [[ -n "${JOLVEN_COMMAND:-${CARTRIDGES_COMMAND:-}}" ]]; then
        printf '%s\n' "${JOLVEN_COMMAND:-${CARTRIDGES_COMMAND:-}}"
        return 0
    fi
    if [[ -x "$HOME/.local/bin/jolven" ]]; then
        printf '%s\n' "$HOME/.local/bin/jolven"
        return 0
    fi
    if [[ -x "$HOME/.local/bin/cartridges" ]]; then
        printf '%s\n' "$HOME/.local/bin/cartridges"
        return 0
    fi
    local found=""
    found="$(command -v jolven || command -v cartridges || true)"
    if [[ -n "$found" ]]; then
        printf '%s\n' "$found"
        return 0
    fi
    if command -v flatpak >/dev/null 2>&1 \
        && flatpak info io.github.zonaro.Jolven >/dev/null 2>&1; then
        printf 'flatpak run io.github.zonaro.Jolven\n'
        return 0
    fi
    printf 'jolven\n'
}

if ! command -v gsettings >/dev/null 2>&1; then
    printf 'gsettings nao encontrado; instale o pacote glib2 (comando gsettings).\n' >&2
    exit 1
fi

COMMAND="$(resolve_command)"
readonly COMMAND

# Descobre entradas personalizadas existentes.
current_list="$(gsettings get "$MEDIA_KEYS_SCHEMA" custom-keybindings)"
# Garante que o path do Jolven esteja na lista, sem duplicar.
if [[ "$current_list" != *"$SHORTCUT_PATH"* ]]; then
    if [[ "$current_list" == "@as []" || "$current_list" == "[]" ]]; then
        new_list="['$SHORTCUT_PATH']"
    else
        # "[ 'a', 'b' ]" -> "[ 'a', 'b', 'jolven/' ]"
        new_list="${current_list%]*}, '$SHORTCUT_PATH']"
    fi
    gsettings set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$new_list"
else
    new_list="$current_list"
fi

# Avisa se outro atalho personalizado ja usa o mesmo binding.
conflict_path=""
existing_paths="$(gsettings get "$MEDIA_KEYS_SCHEMA" custom-keybindings \
    | tr -d "[]'," | tr ' ' '\n' | grep -v '^$' || true)"
for path in $existing_paths; do
    [[ "$path" == "$SHORTCUT_PATH" ]] && continue
    other_binding="$(gsettings get "$CUSTOM_SCHEMA:$path" binding 2>/dev/null || true)"
    if [[ "$other_binding" == "'$BINDING'" ]]; then
        other_name="$(gsettings get "$CUSTOM_SCHEMA:$path" name 2>/dev/null || echo '?')"
        printf 'Aviso: %s (%s) ja usa %s; sera liberado para o Jolven.\n' \
            "$path" "$other_name" "$BINDING"
        conflict_path="$path"
        gsettings set "$CUSTOM_SCHEMA:$path" binding ''
    fi
done

gsettings set "$CUSTOM_SCHEMA:$SHORTCUT_PATH" name "$SHORTCUT_NAME"
gsettings set "$CUSTOM_SCHEMA:$SHORTCUT_PATH" command "$COMMAND"
gsettings set "$CUSTOM_SCHEMA:$SHORTCUT_PATH" binding "$BINDING"

printf 'Atalho global configurado: %s abre "%s" (%s).\n' \
    "$BINDING" "$SHORTCUT_NAME" "$COMMAND"
printf 'Gerencie em Configuracoes > Teclado > Atalhos personalizados.\n'
if [[ -n "$conflict_path" ]]; then
    printf 'Binding anterior em %s foi esvaziado.\n' "$conflict_path"
fi
