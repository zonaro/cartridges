#!/usr/bin/bash
# Install only the system integration owned by Cartridges.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="${PREFIX:-/usr}"
bindir="$prefix/bin"
libexecdir="$prefix/libexec"
app_id="page.redclaw.Cartridges"

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

if [[ -n "${CARTRIDGES_EXECUTABLE:-}" ]]; then
    cartridges_executable="$CARTRIDGES_EXECUTABLE"
elif [[ -x "$HOME/.local/bin/cartridges" ]]; then
    # A user installation takes precedence even when the display manager's
    # minimal PATH happens to find a stale /usr/local copy first.
    cartridges_executable="$HOME/.local/bin/cartridges"
else
    cartridges_executable="$(command -v cartridges || true)"
fi
if [[ -z "$cartridges_executable" && ! -x "$bindir/cartridges" ]]; then
    printf 'Cartridges must be installed before enabling its GDM session.\n' >&2
    printf 'Build it with Meson first, or install with: meson install -C build\n' >&2
    exit 1
fi
[[ -n "$cartridges_executable" ]] || cartridges_executable="$bindir/cartridges"
if [[ ! -x "$cartridges_executable" ]]; then
    printf 'Cartridges executable is not runnable: %s\n' "$cartridges_executable" >&2
    exit 1
fi
cartridges_bindir="$(dirname -- "$cartridges_executable")"

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$tmp_dir"' EXIT
sed -e "s|@bindir@|$cartridges_bindir|g" -e "s|@APP_ID@|$app_id|g" \
    "$repo_dir/session/cartridges-session.in" > "$tmp_dir/cartridges-session"
sed -e "s|@bindir@|$bindir|g" -e "s|@game_session_datadir@|$prefix/share|g" \
    "$repo_dir/session/cartridges-game-mode-setup.in" \
    > "$tmp_dir/cartridges-game-mode-setup"
sed -e "s|@libexecdir@|$libexecdir|g" \
    "$repo_dir/data/page.redclaw.Cartridges.GameMode.policy.in" \
    > "$tmp_dir/page.redclaw.Cartridges.GameMode.policy"

for target in "$bindir/cartridges-session" "$libexecdir/cartridges-game-mode-setup"; do
    if [[ -e "$target" ]] && ! grep -q 'X-Cartridges-Managed=true' "$target"; then
        backup="$target.backup.$(date +%Y%m%d%H%M%S)"
        printf 'Preserving unmanaged file as %s\n' "$backup"
        sudo cp --preserve=mode,timestamps -- "$target" "$backup"
    fi
done

sudo install -d -m 0755 -- "$bindir" "$libexecdir"
sudo install -m 0755 -- "$tmp_dir/cartridges-session" "$bindir/cartridges-session"
sudo install -m 0755 -- "$tmp_dir/cartridges-game-mode-setup" "$libexecdir/cartridges-game-mode-setup"
sudo install -d -m 0755 -- "$prefix/share/polkit-1/actions"
sudo install -m 0644 -- "$tmp_dir/page.redclaw.Cartridges.GameMode.policy" \
    "$prefix/share/polkit-1/actions/page.redclaw.Cartridges.GameMode.policy"
sudo "$libexecdir/cartridges-game-mode-setup" install
session_name="$("$libexecdir/cartridges-game-mode-setup" name)"
printf 'Session:\n✓ %s\n' "$session_name"
printf 'Installed Gaming Mode. Select it from the display manager session chooser.\n'
