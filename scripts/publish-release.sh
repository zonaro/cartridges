#!/usr/bin/env bash
# Publica uma release do Cartridges no GitHub Releases com binario Linux.
#
# Versao no formato YY.DDD.HHMM (ano 2 digitos, dia do ano, hora+minuto 24h
# do momento da publicacao). A mesma versao e congelada via CARTRIDGES_VERSION
# para o entry do metainfo, o build meson, a tag e a release.
#
# Uso:
#   ./scripts/publish-release.sh [--dry-run] [--no-push] [--skip-build]
#       [--notes TEXTO] [--notes-file ARQUIVO] [--allow-dirty] [--yes] [VERSAO]
#
# Exemplos:
#   ./scripts/publish-release.sh                        # versao de agora
#   ./scripts/publish-release.sh --dry-run              # mostra o plano
#   ./scripts/publish-release.sh --notes "Correcoes..." # notas manuais
set -Eeuo pipefail

SCRIPT_DIR="$(dirname -- "$(readlink -f -- "$0")")"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"
cd -- "$REPO_ROOT"

REPOSITORY="${CARTRIDGES_GITHUB_REPOSITORY:-zonaro/cartridges}"
VERSION_PATTERN='^[0-9]{2}\.[0-9]{3}\.[0-9]{4}$'

DRY_RUN=0
NO_PUSH=0
SKIP_BUILD=0
ALLOW_DIRTY=0
ASSUME_YES=0
NOTES=""
NOTES_FILE=""
VERSION_ARG=""

usage() {
    sed -n '2,16p' -- "$0"
}

while (($#)); do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --dry-run) DRY_RUN=1 ;;
        --no-push) NO_PUSH=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --allow-dirty) ALLOW_DIRTY=1 ;;
        --yes) ASSUME_YES=1 ;;
        --notes)
            NOTES="${2:?--notes exige um argumento}"
            shift
            ;;
        --notes-file)
            NOTES_FILE="${2:?--notes-file exige um argumento}"
            shift
            ;;
        --*)
            printf 'Opcao desconhecida: %s\n' "$1" >&2
            usage >&2
            exit 64
            ;;
        *)
            if [[ -n $VERSION_ARG ]]; then
                printf 'Versao duplicada: %s\n' "$1" >&2
                exit 64
            fi
            VERSION_ARG="$1"
            ;;
    esac
    shift
done

for command in git gh python3 tar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Comando necessario nao encontrado: %s\n' "$command" >&2
        exit 69
    fi
done

if [[ -n $NOTES && -n $NOTES_FILE ]]; then
    printf 'Use --notes ou --notes-file, nao ambos.\n' >&2
    exit 64
fi

if [[ -n $VERSION_ARG ]]; then
    VERSION="$VERSION_ARG"
else
    VERSION="$(date +'%y.%j.%H%M')"
fi
if [[ ! $VERSION =~ $VERSION_PATTERN ]]; then
    printf 'Versao invalida: %s (esperado YY.DDD.HHMM, ex: 26.278.1935)\n' "$VERSION" >&2
    exit 64
fi
TAG="v$VERSION"
RELEASE_DATE="$(date +'%F')"
ARCH="$(uname -m)"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    printf 'Tag ja existe localmente: %s\n' "$TAG" >&2
    exit 73
fi
if git ls-remote --tags origin "$TAG" 2>/dev/null | grep -q .; then
    printf 'Tag ja existe no remoto: %s\n' "$TAG" >&2
    exit 73
fi

if [[ -z $(git status --porcelain) ]]; then
    DIRTY=0
else
    DIRTY=1
fi
if ((DIRTY)) && ((!ALLOW_DIRTY)); then
    printf 'Arvore com alteracoes nao commitadas. Commit antes ou use --allow-dirty.\n' >&2
    exit 73
fi

if [[ -n $NOTES_FILE ]]; then
    NOTES="$(cat -- "$NOTES_FILE")"
elif [[ -z $NOTES ]]; then
    LAST_TAG="$(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null || true)"
    if [[ -n $LAST_TAG ]]; then
        NOTES="$(git log --format='- %s' "$LAST_TAG..HEAD" -- . ':!po')"
    fi
    if [[ -z ${NOTES:-} ]]; then
        NOTES="- Atualizacao do Cartridges $VERSION."
    fi
fi

METAINFO="data/page.kramo.Cartridges.metainfo.xml.in"
if ! grep -q '<releases>' "$METAINFO"; then
    printf 'Bloco <releases> nao encontrado em %s\n' "$METAINFO" >&2
    exit 69
fi

printf 'Versao:  %s\n' "$VERSION"
printf 'Tag:     %s\n' "$TAG"
printf 'Data:    %s\n' "$RELEASE_DATE"
printf 'Notas:\n%s\n' "$NOTES"

if ((!ASSUME_YES || DRY_RUN)); then
    :
else
    printf 'Publicar release %s (%s)? [s/N] ' "$TAG" "$REPOSITORY"
    read -r answer
    if [[ $answer != [sSyY] ]]; then
        printf 'Cancelado.\n'
        exit 0
    fi
fi

run() {
    if ((DRY_RUN)); then
        printf '+ %s\n' "$*"
    else
        "$@"
    fi
}

export CARTRIDGES_VERSION="$VERSION"
CHECK_VERSION="$(build-aux/get-version.py)"
if [[ $CHECK_VERSION != "$VERSION" ]]; then
    printf 'get-version.py devolveu %s, esperado %s\n' "$CHECK_VERSION" "$VERSION" >&2
    exit 70
fi

# 1. Registra a release no metainfo (vira o corpo da GitHub Release via CI
#    e aparece no dialogo Sobre do app).
insert_release() {
    local metainfo="$1" version="$2" release_date="$3" notes="$4"
    python3 - "$metainfo" "$version" "$release_date" "$notes" <<'EOF'
import sys
import xml.sax.saxutils as sax

metainfo, version, release_date, notes = (
    sys.argv[1],
    sys.argv[2],
    sys.argv[3],
    sys.argv[4],
)
lines = [line.strip().lstrip("- ").strip() for line in notes.splitlines()]
lines = [sax.escape(line) for line in lines if line]
if not lines:
    lines = [f"Atualizacao do Cartridges {sax.escape(version)}."]
items = "\n".join(f"            <li>{line}</li>" for line in lines)
entry = (
    f"    <release version=\"{sax.escape(version)}\" date=\"{sax.escape(release_date)}\">\n"
    "      <description translate=\"no\">\n"
    "        <ul>\n"
    f"{items}\n"
    "        </ul>\n"
    "      </description>\n"
    "    </release>\n"
)
with open(metainfo, encoding="utf-8") as handle:
    content = handle.read()
marker = "<releases>\n"
assert marker in content, "bloco <releases> nao encontrado"
content = content.replace(marker, marker + entry, 1)
with open(metainfo, "w", encoding="utf-8") as handle:
    handle.write(content)
EOF
}

if ((DRY_RUN)); then
    printf '+ insere <release version="%s" date="%s"> em %s\n' \
        "$VERSION" "$RELEASE_DATE" "$METAINFO"
else
    insert_release "$METAINFO" "$VERSION" "$RELEASE_DATE" "$NOTES"
fi

run git add -- "$METAINFO"
run git commit -m "Versão $VERSION"
run git tag -a "$TAG" -m "Cartridges $VERSION"

# 2. Compila com a versao congelada e empacota a arvore instalada.
DIST_DIR="$REPO_ROOT/dist"
TARBALL="cartridges-linux-$ARCH.tar.gz"
if ((SKIP_BUILD)); then
    printf 'Pulando build (--skip-build).\n'
else
    BUILD_DIR="$(mktemp -d -t cartridges-release-build.XXXXXX)"
    STAGE_DIR="$(mktemp -d -t cartridges-release-stage.XXXXXX)"
    cleanup_build() {
        rm -rf -- "$BUILD_DIR" "$STAGE_DIR"
    }
    trap cleanup_build EXIT
    if ((DRY_RUN)); then
        printf '+ meson setup <tmp> --prefix=/usr --buildtype=release -Dprofile=release\n'
        printf '+ meson compile && DESTDIR=<tmp> meson install\n'
        printf '+ tar -C <stage> -czf %s/%s usr\n' "$DIST_DIR" "$TARBALL"
    else
        meson setup "$BUILD_DIR" "$REPO_ROOT" \
            --prefix=/usr \
            --buildtype=release \
            -Dprofile=release
        meson compile -C "$BUILD_DIR"
        DESTDIR="$STAGE_DIR" meson install -C "$BUILD_DIR"
        mkdir -p -- "$DIST_DIR"
        tar -C "$STAGE_DIR" -czf "$DIST_DIR/$TARBALL" usr
        (cd -- "$DIST_DIR" && sha256sum "$TARBALL" >"$TARBALL.sha256")
        printf '%s\n' "$VERSION" >"$DIST_DIR/VERSION"
        printf 'Tarball: %s/%s\n' "$DIST_DIR" "$TARBALL"
    fi
fi

# 3. Publica tag + release (o workflow publish-release.yml anexa os
#    instaladores Windows/macOS do CI a mesma release).
if ((NO_PUSH || DRY_RUN)); then
    printf 'Pulado: push da tag e criacao da release (%s).\n' \
        "$([[ $DRY_RUN == 1 ]] && printf %s dry-run || printf %s --no-push)"
else
    git push origin HEAD
    git push origin "$TAG"
    if ((SKIP_BUILD)); then
        gh release create "$TAG" --repo "$REPOSITORY" \
            --title "Cartridges $VERSION" --notes "$NOTES"
    else
        gh release create "$TAG" --repo "$REPOSITORY" \
            --title "Cartridges $VERSION" --notes "$NOTES" \
            "$DIST_DIR/$TARBALL" "$DIST_DIR/$TARBALL.sha256" "$DIST_DIR/VERSION"
    fi
fi

printf '\nPronto: %s (%s)\n' "$TAG" "$VERSION"
