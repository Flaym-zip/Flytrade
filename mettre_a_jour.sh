#!/usr/bin/env bash
# Rename the application/Compose project, retaining the ACTUAL old data volume.
set -Eeuo pipefail
umask 077
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$HOME/Projets/Flytrade}"
[[ -d "$TARGET" && -f "$TARGET/compose.yaml" && -f "$TARGET/app/brain.py" ]] || {
  echo "Projet existant introuvable : $TARGET" >&2; exit 1;
}
TARGET="$(cd -- "$TARGET" && pwd)"
[[ "$SOURCE" != "$TARGET" && "$SOURCE" != "$TARGET"/* && "$TARGET" != "$SOURCE"/* ]] || {
  echo "Extraire la mise a jour dans un AUTRE dossier non imbrique." >&2; exit 1;
}
command -v docker >/dev/null || { echo "Docker est introuvable." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 est necessaire." >&2; exit 1; }
command -v tar >/dev/null || { echo "tar est necessaire." >&2; exit 1; }
if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
"${DOCKER[@]}" info >/dev/null
compose() { "${DOCKER[@]}" compose --project-directory "$TARGET" -f "$TARGET/compose.yaml" "$@"; }
CID="$(compose ps -a -q lab)"
[[ -n "$CID" && "$CID" != *$'\n'* ]] || {
  echo "Un seul conteneur lab doit exister. Demarrer l'ancien projet avec docker compose up -d." >&2; exit 1;
}
INSPECT="$("${DOCKER[@]}" inspect "$CID")"
WAS_RUNNING="$(python3 -c 'import json,sys;print("yes" if json.load(sys.stdin)[0].get("State",{}).get("Running",False) else "no")' <<< "$INSPECT")"
META="$(python3 -c '
import json,re,sys
v=json.load(sys.stdin)[0]
mount=[m for m in v["Mounts"] if m["Destination"]=="/app/data"]
assert len(mount)==1 and mount[0]["Type"]=="volume", "Volume nomme attendu ; migration dun bind mount non prise en charge."
name=mount[0]["Name"]; assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*",name)
ports=v["HostConfig"]["PortBindings"]["8000/tcp"]
assert len(ports)==1, "Un seul port HTTP attendu."
port=int(ports[0]["HostPort"]); assert 1<=port<=65535
project=v["Config"]["Labels"]["com.docker.compose.project"]
print(json.dumps({"volume":name,"port":port,"project":project}))
' <<< "$INSPECT")"
# Refuse to take over an unrelated Flytrade container.
OTHER="$("${DOCKER[@]}" ps -a --no-trunc --filter label=com.docker.compose.project=flytrade --format '{{.ID}}')"
FULLCID="$(python3 -c 'import json,sys;print(json.load(sys.stdin)[0]["Id"])' <<< "$INSPECT")"
[[ -z "$OTHER" || "$OTHER" == "$FULLCID" ]] || {
  echo "Un autre projet Compose flytrade existe. Aucun changement effectue." >&2; exit 1;
}
BACKUP="$(dirname -- "$TARGET")/_archives/Flytrade/backups/flytrade-sauvegarde-$(date +%Y%m%d-%H%M%S)-$$"
# Verify the user's LOCAL baseline, never replace it by conversation copies.
BASELINE="$TARGET/baselines/100-trades-v1"
if [[ -f "$BASELINE/SHA256SUMS" ]]; then
  echo "Verification SHA-256 de la baseline locale :"
  (cd "$BASELINE" && sha256sum -c SHA256SUMS)
fi
printf '\nSource : %s\nCible  : %s\nCopie  : %s\n' "$SOURCE" "$TARGET" "$BACKUP"
printf 'Volume et port detectes : %s\n' "$META"
echo "Le projet Compose sera nomme flytrade. Le dossier cible ne sera pas renomme."
echo "Le volume original sera reutilise ; le code, .env et toutes les donnees seront sauvegardes."
echo "Terminer les essais et arreter le mode auto avant de confirmer."
echo "La source explicite dans .env est conservee ; sans valeur, le defaut reste Kraken."
echo "Kraken utilise sources/kraken : son corpus, ses poids et son portefeuille sont separes."
read -r -p "Installer Flytrade alpha 08 (correctif sauvegarde SQLite, donnees conservees) ? Ecrire oui : " ANSWER
[[ "$ANSWER" == "oui" || "$ANSWER" == "OUI" ]] || { echo "Annule, aucun changement."; exit 0; }
# Only restart the old container if THIS script stopped a running service and
# failed before changing the application code. Do not improvise a rollback once
# code replacement or the new application has started.
STOP_REQUESTED=0
CODE_REPLACEMENT_STARTED=0
update_failed() {
  local status=$?
  trap - ERR
  set +e
  echo "Mise a jour interrompue. Ne pas supprimer le volume. Sauvegarde : $BACKUP" >&2
  if [[ "$CODE_REPLACEMENT_STARTED" == 0 ]]; then
    echo "Le remplacement du code n'a pas commence ; les donnees du volume n'ont pas ete remplacees." >&2
    if [[ "$STOP_REQUESTED" == 1 && "$WAS_RUNNING" == yes ]]; then
      echo "Tentative de redemarrage du conteneur precedent (sans reconstruction)." >&2
      if "${DOCKER[@]}" start "$CID"; then
        echo "Ancien conteneur redemarre. Verifier son etat et ses logs." >&2
      else
        echo "Redemarrage non confirme. Utiliser docker compose start lab depuis le dossier du projet." >&2
      fi
    else
      echo "Pour reprendre l'ancien service : docker compose start lab, depuis le dossier du projet." >&2
    fi
  else
    echo "Le remplacement du code avait commence. Consulter la procedure de retour avant de redemarrer." >&2
  fi
  echo "Procedure de retour : LIRE-MOI-MISE-A-JOUR.md" >&2
  exit "$status"
}
trap update_failed ERR
mkdir -p "$BACKUP/data"
cp -a "$TARGET" "$BACKUP/source"
printf '%s\n' "$TARGET" > "$BACKUP/target.txt"
printf '%s\n' "$META" > "$BACKUP/docker-meta.json"
STOP_REQUESTED=1
compose stop lab
# Docker may run via sudo. Extract its tar stream as the shell's user, not root.
# The original cp-to-directory made root-owned backups; SQLite's read-only WAL
# validation then failed when it needed a writable -shm file or directory.
# pipefail also prevents validating a truncated/failed Docker copy.
"${DOCKER[@]}" cp "$CID:/app/data/." - | \
  tar --extract --file=- --directory="$BACKUP/data" --no-same-owner --no-same-permissions
python3 - "$BACKUP/data" <<'PYBACK'
import hashlib,sqlite3,json,sys,shutil,tempfile,os,stat
from pathlib import Path
root=Path(sys.argv[1])
entries=list(root.rglob('*'))
if any(p.is_symlink() or not (p.is_dir() or p.is_file()) for p in entries):
    raise ValueError('Sauvegarde : liens ou fichiers speciaux non admis')
files=[p for p in entries if p.is_file()]
assert files, 'Sauvegarde des donnees vide : arret'
# Validate a temporary working copy, INCLUDING its WAL/SHM/journal companions.
# This leaves the raw backup byte-for-byte intact even if SQLite creates or
# updates auxiliary files while reading. Never use immutable=1 to hide a WAL.
for p in sorted(root.rglob('*.sqlite3')):
    with tempfile.TemporaryDirectory(prefix='.sqlite-check-',dir=root.parent) as temp:
        work=Path(temp)/p.name
        for suffix in ('','-wal','-shm','-journal'):
            src=p.with_name(p.name+suffix)
            if src.exists():
                dst=Path(str(work)+suffix)
                shutil.copyfile(src,dst)
                os.chmod(dst,stat.S_IRUSR|stat.S_IWUSR)
        con=sqlite3.connect(work.absolute().as_uri()+'?mode=ro',uri=True)
        try:
            result=con.execute('PRAGMA quick_check').fetchall()
            if result != [('ok',)]:
                raise ValueError('Base de donnees endommagee : '+str(p)+': '+repr(result))
        finally:
            con.close()
    print('SQLite OK : '+str(p.relative_to(root)))
for p in root.rglob('*.json'):
    json.loads(p.read_text())
files=sorted(p for p in root.rglob('*') if p.is_file())
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
(root.parent/'data-SHA256SUMS').write_text(''.join(digest(p)+'  '+str(p.relative_to(root))+'\n' for p in files))
print('Sauvegarde lisible ; empreintes enregistrees.')
PYBACK
CODE_REPLACEMENT_STARTED=1
python3 - "$SOURCE" "$TARGET" "$META" <<'PY'
import json,re,shutil,sys
from pathlib import Path
source,target=map(Path,sys.argv[1:3]);meta=json.loads(sys.argv[3])
items=['app','tests','scripts','docs','Dockerfile','compose.yaml','requirements.txt',
       'requirements-dev.txt','pytest.ini','.dockerignore','.env.example','.gitignore',
       'README.md','audit-alpha05.json','MODELE.md','VALIDATION.md','CHANGELOG.md','LIRE-MOI-MISE-A-JOUR.md','SHA256SUMS.txt',
       'mettre_a_jour.sh','restaurer_sauvegarde.sh','install_docker_kubuntu.sh','CORRECTIF-INSTALLATION-08.md']
for name in items:
    src,dst=source/name,target/name
    if not src.exists():continue
    if src.is_dir():
        if dst.exists():shutil.rmtree(dst)
        shutil.copytree(src,dst,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.pytest_cache'))
    else:shutil.copy2(src,dst)
p=target/'.env';text=p.read_text() if p.exists() else ''
values={'FLYTRADE_VOLUME':meta['volume'],'FLYTRADE_VOLUME_EXTERNAL':'true',
        'FLYTRADE_PORT':str(meta['port']),'COMPOSE_PROJECT_NAME':'flytrade'}
for key,val in values.items():
    pattern=r'(?m)^\s*(?:export\s+)?'+re.escape(key)+r'\s*=.*$'
    if re.search(pattern,text):text=re.sub(pattern,key+'='+val,text)
    else:text=text.rstrip()+'\n'+key+'='+val+'\n'
p.write_text(text)
print('Nouveau nom flytrade ; ancien volume et port conserves dans .env.')
PY
export COMPOSE_PROJECT_NAME=flytrade
compose config --quiet
compose up --build -d --wait --wait-timeout 180
# A manually stopped old container could otherwise restart after reboot via
# unless-stopped if started separately. Keep it stopped and explicitly inert.
OLDPROJECT="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["project"])' <<< "$META")"
if [[ "$OLDPROJECT" != "flytrade" ]]; then
  "${DOCKER[@]}" update --restart=no "$CID"
fi
compose ps
printf '\nFlytrade installe. Sauvegarde : %s\n' "$BACKUP"
echo "Ouvrir le port habituel (8088 par defaut), puis Ctrl+Maj+R."
if [[ -f "$BASELINE/SHA256SUMS" ]]; then
  (cd "$BASELINE" && sha256sum -c SHA256SUMS)
fi
echo "Aucune base ni calibration Coinbase ne migre silencieusement vers Kraken."
echo "Tutoriel : /guide ; Wiki : /wiki. Poids et corpus du profil actuel conserves."
echo "Retour au profil historique : FLYTRADE_FEED=coinbase dans .env, puis docker compose up -d."
echo "Anciennes sauvegardes et baselines non modifiees."
echo "Aucun argent reel, aucune cle API, aucun ordre."
