#!/usr/bin/bash
# One-time privileged setup for the xCloud streaming dependencies helper.
# Installs the polkit policy so the app can install GStreamer WebRTC plugins
# itself (with a password dialog) instead of failing with "ICE indisponível".
#
# Usage: ./scripts/install-xcloud-deps.sh   (asks for sudo when needed)
set -euo pipefail

cd -- "$(dirname -- "$0")/.."

policy_src="build/data/io.github.zonaro.Jolven.XCloudDeps.policy"
if [[ ! -f "$policy_src" ]]; then
    printf 'Policy file not found at %s. Run: meson setup build ... && meson compile -C build\n' "$policy_src" >&2
    exit 69
fi

# O helper já vai para o libexec via `meson install -C build` (sem root).
# Aqui só a policy precisa de root, como no install-game-session.sh.
for prefix in /usr/local /usr; do
    target="$prefix/share/polkit-1/actions/io.github.zonaro.Jolven.XCloudDeps.policy"
    if [[ -f "$target" ]]; then
        printf 'Policy already present at %s\n' "$target"
        exit 0
    fi
done

sudo install -d -m 0755 -- /usr/share/polkit-1/actions
sudo install -m 0644 -- "$policy_src" \
    /usr/share/polkit-1/actions/io.github.zonaro.Jolven.XCloudDeps.policy
printf 'Policy installed. The app can now install streaming dependencies itself.\n'
