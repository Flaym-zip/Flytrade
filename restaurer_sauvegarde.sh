#!/usr/bin/env bash
# Roll back source AND all authoritative data, including the live SQLite store.
set -Eeuo pipefail
umask 077
BACKUP="${1:?Usage: bash restaurer_sauvegarde.sh SAUVEGARDE PROJET}"
TARGET="${2:-$HOME/Projets/Flytrade}"
[[ -f "$BACKUP/source/compose.yaml" && -f "$BACKUP/docker-meta.json" ]] || {
  echo "Sauvegarde incomplete ; arret sans modification." >&2; exit 1;
}
[[ -d "$TARGET" && -f "$TARGET/compose.yaml" ]] || { echo "Projet cible introuvable." >&2; exit 1; }
BACKUP="$(cd -- "$BACKUP" && pwd)"; TARGET="$(cd -- "$TARGET" && pwd)"
[[ "$BACKUP" != "$TARGET" && "$BACKUP" != "$TARGET"/* ]] || { echo "Chemins incompatibles." >&2; exit 1; }
if [[ -f "$BACKUP/data-SHA256SUMS" ]]; then
  (cd "$BACKUP/data" && sha256sum -c ../data-SHA256SUMS)
fi
if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
"${DOCKER[@]}" info >/dev/null
compose() { "${DOCKER[@]}" compose --project-directory "$TARGET" -f "$TARGET/compose.yaml" "$@"; }
printf 'Restaurer code ET memoire depuis %s vers %s\n' "$BACKUP" "$TARGET"
echo "Les apprentissages posterieurs a la sauvegarde ne seront plus actifs."
read -r -p "Confirmer en ecrivant restaurer : " ANSWER
[[ "$ANSWER" == "restaurer" ]] || { echo "Annule."; exit 0; }
FAILED="$(dirname -- "$TARGET")/_archives/Flytrade/backups/avant-restauration-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$FAILED/data"
CID="$(compose ps -a -q lab)"
compose stop lab
if [[ -n "$CID" ]]; then
  "${DOCKER[@]}" cp "$CID:/app/data/." - | \
    tar --extract --file=- --directory="$FAILED/data" --no-same-owner --no-same-permissions
  "${DOCKER[@]}" update --restart=no "$CID"
fi
cp -a "$TARGET" "$FAILED/source"
mv "$TARGET" "$FAILED/code-deplace"
cp -a "$BACKUP/source" "$TARGET"
python3 - "$BACKUP/data" "$FAILED/restauration.tar" <<'PY'
import sys,tarfile
from pathlib import Path
root=Path(sys.argv[1])
with tarfile.open(sys.argv[2],'w') as tar:
    for p in root.rglob('*'):
        if p.is_symlink():raise ValueError('Liens symboliques non admis dans la sauvegarde')
        if p.is_file():tar.add(p,arcname=str(p.relative_to(root)),recursive=False)
PY
compose build lab
compose run --rm --no-deps -T --entrypoint python lab -c '
import io,json,os,sys,tarfile,tempfile,shutil
from pathlib import Path
root=Path("/app/data")
staged=tempfile.TemporaryFile(dir=root)
shutil.copyfileobj(sys.stdin.buffer,staged,1024*1024)
staged.seek(0)
with tarfile.open(fileobj=staged,mode="r:") as tar:
    members=tar.getmembers()
    for member in members:
        dest=(root/member.name).resolve()
        if not member.isfile() or not dest.is_relative_to(root):raise ValueError("Archive invalide")
    assert members, "Sauvegarde vide"
    # No old WAL file may coexist with a restored database.
    for name in ["state.json"]+[base+suffix for base in ("flytrade-live.sqlite3","flytrade-motifs-live.sqlite3","flytrade-training.sqlite3","flytrade-decision05.sqlite3","flytrade-academy05.sqlite3","flytrade06.sqlite3","flytrade-training06.sqlite3") for suffix in ("","-wal","-shm")]:
        for directory in (root, root/"sources"/"kraken"):
            path=directory/name
            if path.exists():path.unlink()
    for member in members:
        dest=root/member.name;dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open("wb") as f:
            f.write(tar.extractfile(member).read());f.flush();os.fsync(f.fileno())
' < "$FAILED/restauration.tar"
compose up -d --force-recreate --wait --wait-timeout 180
printf 'Code et memoire restaures. Etat abandonne conserve dans %s\n' "$FAILED"
