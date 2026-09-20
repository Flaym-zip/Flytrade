#!/usr/bin/env bash
# Installs Docker Engine + Compose from the official Ubuntu repository.
# No curl | bash, no automatic removal of existing packages, no docker group grant.
set -Eeuo pipefail
trap 'printf "\nInstallation interrompue (ligne %s). Ne pas ignorer cette erreur.\n" "$LINENO" >&2' ERR

if [[ ! -r /etc/os-release ]]; then
  echo "Impossible d'identifier la distribution." >&2; exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [[ "${ID:-}" != "ubuntu" && " ${ID_LIKE:-} " != *" ubuntu "* ]]; then
  echo "Ce script vise Kubuntu/Ubuntu. Systeme detecte : ${PRETTY_NAME:-inconnu}." >&2
  exit 1
fi
case "$CODENAME" in
  jammy|noble|resolute) ;;
  *) echo "Base Ubuntu detectee : $CODENAME. Ce script cible 22.04, 24.04 ou 26.04." >&2
     echo "Arret volontaire : ne pas remplacer le nom de version au hasard." >&2
     echo "Verifier https://docs.docker.com/engine/install/ubuntu/" >&2; exit 1 ;;
esac
SUDO=()
if (( EUID != 0 )); then
  command -v sudo >/dev/null || { echo "sudo est necessaire." >&2; exit 1; }
  SUDO=(sudo)
fi
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in amd64|arm64) ;; *) echo "Architecture non testee par ce projet : $ARCH" >&2; exit 1;; esac
printf 'Systeme : %s\nBase : %s / %s\n' "${PRETTY_NAME:-Ubuntu}" "$CODENAME" "$ARCH"
echo "Installation de Docker Engine, Buildx et Compose. Activation du service au demarrage."
echo "Aucun ajout au groupe docker. Les commandes continueront a utiliser sudo."
read -r -p "Continuer ? [o/N] " answer
case "$answer" in o|O|oui|y|Y|yes) ;; *) echo "Annule."; exit 0;; esac

conflicts=()
for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
  if [[ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" == "installed" ]]; then
    conflicts+=("$package")
  fi
done
if (( ${#conflicts[@]} )); then
  printf '\nPaquets en conflit detectes : %s\n' "${conflicts[*]}" >&2
  echo "Arret : verifier leurs dependances avant de les desinstaller manuellement." >&2
  echo "Aucun paquet n'a ete supprime par ce script." >&2
  exit 1
fi
if [[ -e /etc/apt/sources.list.d/docker.list ]]; then
  echo "Une ancienne source docker.list existe. La verifier avant de continuer." >&2
  exit 1
fi
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y ca-certificates curl
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl --fail --silent --show-error --location --max-time 30 \
  "https://download.docker.com/linux/ubuntu/dists/$CODENAME/Release" -o "$TMP/Release"
curl --fail --silent --show-error --location --max-time 30 \
  https://download.docker.com/linux/ubuntu/gpg -o "$TMP/docker.asc"
"${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
"${SUDO[@]}" install -m 0644 "$TMP/docker.asc" /etc/apt/keyrings/docker.asc
cat > "$TMP/docker.sources" <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $CODENAME
Components: stable
Architectures: $ARCH
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
if [[ -e /etc/apt/sources.list.d/docker.sources ]] && ! cmp -s "$TMP/docker.sources" /etc/apt/sources.list.d/docker.sources; then
  echo "La source docker.sources existante est differente. Arret pour eviter de l'ecraser." >&2; exit 1
fi
"${SUDO[@]}" install -m 0644 "$TMP/docker.sources" /etc/apt/sources.list.d/docker.sources
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
"${SUDO[@]}" systemctl enable --now docker
"${SUDO[@]}" docker run --rm hello-world
"${SUDO[@]}" docker compose version
printf '\nDocker est pret. Dans le dossier du projet :\n  sudo docker compose up --build -d\n'
printf 'Interface : http://127.0.0.1:8088\n'
