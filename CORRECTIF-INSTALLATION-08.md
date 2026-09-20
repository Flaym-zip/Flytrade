# Flytrade alpha 08 - correctif installation 1 (2026-09-20)

Ce correctif ne modifie aucun fichier de l'application, aucun calcul neuronal,
aucune regle de trading ni le format des bases. Il corrige la sauvegarde faite
par mettre_a_jour.sh et la copie de securite de restaurer_sauvegarde.sh.

## Incident observe

Apres l'arret du service et la copie de /app/data, la verification SQLite echoue :

    sqlite3.OperationalError: attempt to write a readonly database

Le script original executait docker cp via sudo puis la verification SQLite
avec l'utilisateur ordinaire. La copie appartenait alors a root. Meme avec une
ouverture mode=ro, une base WAL peut necessiter un fichier auxiliaire -shm ou
un dossier accessible en ecriture. Ce cas a ete reproduit avec une vraie base
SQLite et deux identites Unix distinctes.

Dans le journal fourni, cette erreur intervient AVANT le remplacement du code,
la modification de .env et le lancement de la nouvelle image. Le volume original
n'a pas ete remplace. Le message de copie ne prouve pas que la sauvegarde ait
termine sa validation. Conserver la copie interrompue sans la restaurer.

## Remise en service de l'ancien conteneur

    cd "$HOME/Projets/Flytrade"
    sudo docker compose ps -a
    sudo docker compose start lab
    sudo docker compose ps -a

start utilise le conteneur existant, sans reconstruction. Ne pas utiliser de
commande de suppression. Si ce redemarrage echoue, lire :

    sudo docker compose logs --tail=80 lab

## Installer l'archive corrigee

Arreter collecte, politique et atelier, et laisser finir les fenetres ouvertes.
Extraire la nouvelle archive dans un dossier distinct, jamais sur le projet actif.

    mkdir -p "$HOME/Projets/_archives/Flytrade/updates/alpha08-correctif"
    unzip -o "$(xdg-user-dir DOWNLOAD)/flytrade-alpha08-correctif-installation.zip" \
      -d "$HOME/Projets/_archives/Flytrade/updates/alpha08-correctif"
    bash "$HOME/Projets/_archives/Flytrade/updates/alpha08-correctif/flytrade/mettre_a_jour.sh" \
      "$HOME/Projets/Flytrade"

Lancer le script avec bash en tant qu'utilisateur ordinaire, pas avec sudo bash.
Il appelle sudo uniquement pour Docker si necessaire. Une nouvelle sauvegarde
est creee ; la sauvegarde interrompue reste conservee.

## Changements precis

- docker cp produit un flux tar. tar l'extrait avec l'utilisateur du script,
  --no-same-owner et --no-same-permissions, sous umask 077. Les copies ne sont
  donc plus involontairement possedees par root lorsque Docker utilise sudo.
- Chaque verification SQLite porte sur une copie temporaire privee, accompagnee
  de ses fichiers -wal, -shm et -journal lorsqu'ils existent. La copie brute de
  sauvegarde reste inchangee et ses empreintes SHA-256 sont ensuite enregistrees.
- Aucune option immutable=1 n'est utilisee pour masquer le probleme. Aucun
  journal WAL du volume ou de la sauvegarde brute n'est supprime.
- Une copie incomplete ou une base corrompue bloque toujours la mise a jour.
- Si une erreur survient avant le remplacement du code, l'ancien conteneur est
  redemarre automatiquement SEULEMENT s'il fonctionnait avant cette tentative.
- Si le code avait commence a etre remplace, le script ne tente pas de rollback
  improvise. Il renvoie a la procedure de restauration.
- La copie de securite faite avant restauration utilise aussi le flux tar avec
  le bon proprietaire. Le contenu de la restauration n'est pas redefini.

## Validation du correctif

Environnement local Linux, Python 3.13.5, pytest 9.0.2.

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
      python -m pytest tests/test_update_scripts.py -q
    13 passed in 19.27s

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
      python -m pytest -q --ignore=tests/test_update_scripts.py
    162 passed in 31.90s

Les 175 tests ont donc ete executes en deux lots. Une execution regroupee a
atteint le delai maximal de l'outil ; elle n'est pas presentee comme terminee.

Cas de regression : erreur readonly reproduite avec UID 65534 face a une copie
root-owned, puis mise a jour corrigee reussie sous cette meme identite ; WAL
contenant une ligne valide non encore integree au fichier principal ; octets
originaux de sauvegarde preserves ; empreintes verifiees ; corruption refusee ;
echec de copie ; redemarrage conditionnel ; annulation ; .env et baseline conserves.

Les 37 fichiers sous app/ sont identiques octet pour octet a l'archive alpha 08
precedente. Docker n'est pas disponible dans cet environnement : son transport
et ses commandes sont testes avec un double. SQLite et les permissions Unix
sont reels. Aucune validation d'un moteur Docker reel ou du poste de l'utilisateur
n'est revendiquee.

## Sources techniques

Docker cp (proprietaire des copies et flux tar) :
https://docs.docker.com/reference/cli/docker/container/cp/
SQLite WAL, section Read-Only Databases :
https://sqlite.org/wal.html
Docker compose start :
https://docs.docker.com/reference/cli/docker/compose/start/
Docker compose ps (inclure les conteneurs arretes avec -a) :
https://docs.docker.com/reference/cli/docker/compose/ps/

Ne pas executer docker compose down -v, docker volume prune ou chmod -R 777.
Le volume flylab-alpha_memory, les cerveaux et les donnees n'ont pas besoin d'un reset.
