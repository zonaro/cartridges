#!/usr/bin/bash
# Install only the system integration owned by Jolven.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="${PREFIX:-/usr}"
bindir="$prefix/bin"
libexecdir="$prefix/libexec"
app_id="io.github.zonaro.Jolven"

if [[ ! -r /etc/os-release ]]; then
    printf 'Cannot identify this operating system.\n' >&2
    exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != fedora ]]; then
    printf 'This installer currently supports Fedora; use the Meson install on other distributions.\n' >&2
    exit 1
fi

missing=()
for package in gamescope gamemode libmanette desktop-file-utils polkit wireplumber; do
    rpm -q "$package" >/dev/null 2>&1 || missing+=("$package")
done
if (( ${#missing[@]} )); then
    printf 'Installing required Fedora packages: %s\n' "${missing[*]}"
    sudo dnf install --refresh --assumeyes "${missing[@]}"
fi
printf 'Dependencies:\n'
for package in gamescope gamemode libmanette desktop-file-utils polkit wireplumber; do
    if ! rpm -q "$package" >/dev/null 2>&1; then
        printf '✗ %s\n' "$package" >&2
        exit 1
    fi
    printf '✓ %s\n' "$package"
done

if [[ -n "${JOLVEN_EXECUTABLE:-${CARTRIDGES_EXECUTABLE:-}}" ]]; then
    jolven_executable="${JOLVEN_EXECUTABLE:-${CARTRIDGES_EXECUTABLE:-}}"
elif [[ -x "$HOME/.local/bin/jolven" ]]; then
    # A user installation takes precedence even when the display manager's
    # minimal PATH happens to find a stale /usr/local copy first.
    jolven_executable="$HOME/.local/bin/jolven"
elif [[ -x "$HOME/.local/bin/cartridges" ]]; then
    # Compat: fork anterior ainda instalado no usuario
    jolven_executable="$HOME/.local/bin/cartridges"
else
    jolven_executable="$(command -v jolven || command -v cartridges || true)"
fi
if [[ -z "$jolven_executable" && ! -x "$bindir/jolven" ]]; then
    printf 'Jolven must be installed before enabling its display-manager session.\n' >&2
    printf 'Build it with Meson first, or install with: meson install -C build\n' >&2
    exit 1
fi
[[ -n "$jolven_executable" ]] || jolven_executable="$bindir/jolven"
if [[ ! -x "$jolven_executable" ]]; then
    printf 'Jolven executable is not runnable: %s\n' "$jolven_executable" >&2
    exit 1
fi
jolven_bindir="$(dirname -- "$jolven_executable")"

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir"' EXIT
sed -e "s|@bindir@|$jolven_bindir|g" -e "s|@APP_ID@|$app_id|g" \
    "$repo_dir/session/jolven-session.in" > "$tmp_dir/jolven-session"
sed -e "s|@bindir@|$bindir|g" -e "s|@game_session_datadir@|$prefix/share|g" \
    "$repo_dir/session/jolven-game-mode-setup.in" \
    > "$tmp_dir/jolven-game-mode-setup"
sed -e "s|@libexecdir@|$libexecdir|g" \
    "$repo_dir/data/io.github.zonaro.Jolven.GameMode.policy.in" \
    > "$tmp_dir/io.github.zonaro.Jolven.GameMode.policy"

for target in "$bindir/jolven-session" "$libexecdir/jolven-game-mode-setup"; do
    if [[ -e "$target" ]] && ! grep -q 'X-\(Jolven\|Cartridges\)-Managed=true' "$target"; then
        backup="$target.backup.$(date +%Y%m%d%H%M%S)"
        printf 'Preserving unmanaged file as %s\n' "$backup"
        sudo cp --preserve=mode,timestamps -- "$target" "$backup"
    fi
done

sudo install -d -m 0755 -- "$bindir" "$libexecdir"
sudo install -m 0755 -- "$tmp_dir/jolven-session" "$bindir/jolven-session"
sudo install -m 0755 -- "$tmp_dir/jolven-game-mode-setup" "$libexecdir/jolven-game-mode-setup"
sudo install -d -m 0755 -- "$prefix/share/polkit-1/actions"
sudo install -m 0644 -- "$tmp_dir/io.github.zonaro.Jolven.GameMode.policy" \
    "$prefix/share/polkit-1/actions/io.github.zonaro.Jolven.GameMode.policy"
sudo "$libexecdir/jolven-game-mode-setup" install
session_name="$("$libexecdir/jolven-game-mode-setup" name)"
printf 'Session:\n✓ %s\n' "$session_name"
printf 'Installed Jolven Session. Select it from the display manager session chooser.\n'
