#!/usr/bin/env bash
# Instala o Cartridges no usuario atual sem manter uma copia do repositorio.
set -Eeuo pipefail

readonly REPOSITORY_URL="${CARTRIDGES_REPOSITORY_URL:-https://github.com/zonaro/cartridges.git}"
readonly REPOSITORY_REF="${CARTRIDGES_REF:-main}"
readonly INSTALL_PREFIX="${PREFIX:-$HOME/.local}"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    printf 'Nao execute este instalador como root ou com sudo.\n' >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    printf 'Nao foi possivel identificar a distribuicao Linux.\n' >&2
    exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release
if [[ ${ID:-} != fedora ]]; then
    printf 'Este instalador atualmente oferece suporte ao Fedora.\n' >&2
    exit 1
fi

for command in sudo dnf; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Comando necessario nao encontrado: %s\n' "$command" >&2
        exit 1
    fi
done

readonly -a REQUIRED_PACKAGES=(
    desktop-file-utils
    gcc
    gettext
    git
    gtk4-devel
    libadwaita-devel
    meson
    ninja-build
    python3-gobject
    python3-pillow
    python3-pyyaml
    python3-requests
)

missing_packages=()
for package in "${REQUIRED_PACKAGES[@]}"; do
    rpm -q "$package" >/dev/null 2>&1 || missing_packages+=("$package")
done

if (( ${#missing_packages[@]} )); then
    printf 'Instalando dependencias necessarias...\n'
    sudo dnf install --refresh --assumeyes "${missing_packages[@]}"
else
    printf 'Todas as dependencias ja estao instaladas.\n'
fi

work_dir="$(mktemp -d -t cartridges-install.XXXXXX)"
cleanup() {
    rm -rf -- "$work_dir"
}
trap cleanup EXIT

printf 'Baixando o Cartridges...\n'
git clone --depth 1 --branch "$REPOSITORY_REF" --single-branch \
    "$REPOSITORY_URL" "$work_dir/cartridges"

printf 'Compilando...\n'
meson setup "$work_dir/cartridges/build" "$work_dir/cartridges" \
    --prefix="$INSTALL_PREFIX" \
    --buildtype=release \
    -Dprofile=release
meson compile -C "$work_dir/cartridges/build"

printf 'Instalando em %s...\n' "$INSTALL_PREFIX"
meson install -C "$work_dir/cartridges/build"

printf '\nCartridges instalado com sucesso. Abra-o pelo menu de aplicativos.\n'
if [[ ":$PATH:" != *":$INSTALL_PREFIX/bin:"* ]]; then
    printf 'Se necessario, adicione %s/bin ao PATH para usar o comando cartridges.\n' \
        "$INSTALL_PREFIX"
fi
