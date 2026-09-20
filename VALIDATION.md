# Verification du correctif installation 1

13 tests installation/restauration et 162 autres tests passent en deux lots.
Reproduction reelle du defaut de permissions SQLite sous une autre identite Unix.
Transport Docker simule. Details et limites : CORRECTIF-INSTALLATION-08.md.

---

# Validation d'alpha 08 - 20 septembre 2026

## Base et portee

Source de travail : archive Flytrade.zip fournie par l'utilisateur (alpha 07).
Reference visuelle : maquette HTML et palette de Trading sur Euphoria.zip.
La maquette n'est pas un frontend connecte au moteur : ses scripts de demonstration,
faux chiffres et dependances externes ne sont pas integres. La palette et la
hierarchie sont adaptees dans la vraie application FastAPI/JavaScript.

Aucune modification des equations de prevision, de la politique de risque, des
cotations ou du fournisseur de prix. Les nouvelles classes Academy08 et Run08
encadrent le cycle de vie des instances existantes.

## Tests automatiques

- 162 tests Python moteur/API/donnees/Wiki : passes (21,42 s lors du dernier run).
- 9 tests installation/restauration : passes avec un DOUBLE DE DOCKER (79,23 s).
- Total : 171 tests. Les 24 tests alpha08 sont inclus, pas ajoutes une seconde fois.
- Verification de syntaxe Node pour clarity08.js et wiki08.js : reussie.

Les tests alpha08 couvrent notamment :

- verification du corpus sans mutation ; conservation du test cache ;
- identite du plan et des corrections avec alpha07 pour les trois parcours ;
- reprise au meme index apres pause et redemarrage ;
- relecture : plan identique, graine identique, poids initiaux identiques ;
- continuation : poids conserves, seul TRAIN corrige, recalibration neuve ;
- exclusion des nouvelles dates importees d'une continuation sur TRAIN sauvegarde ;
- conservation du registre des tests ouverts apres reset, recreation et redemarrage ;
- migration additive des anciennes seances, sans conversion de poids ;
- archivage, rollback en cas d'echec d'ecriture, refus pendant une activite ;
- reset cerveau live sans toucher au capital ; confirmations API et origine HTTP ;
- ancres Wiki, identifiants uniques, 29 chapitres et absence de runtime de maquette.

## Interface Chromium

Chromium systeme est disponible. La navigation HTTP directe du navigateur est
bloquee par la politique de l'environnement (ERR_BLOCKED_BY_ADMINISTRATOR).
Un banc charge donc les vrais HTML/CSS/JS et remplace uniquement le transport
fetch par un pont Python vers l'API FastAPI locale. history.replaceState est
neutralise dans ce banc. Les routes /guide, /training et /wiki sont testees aussi
par le client HTTP Python. Cela ne teste pas une navigation HTTP navigateur
ordinaire de bout en bout sur l'installation Docker de l'utilisateur.

15 controles interactifs ont abouti : verification sans mutation, annulation,
creation vierge, pas unique, reprise/pause, relecture, fin TRAIN/calibration,
continuation avec poids conserves, recalibration, test, deploiement, annulation
reset, annulation par Echap, archives visibles, reset atelier avec donnees/live
conserves. Aucun pageerror JavaScript non intercepte dans ce parcours.

Ces controles utilisent une COPIE de l'export alpha07 de l'archive utilisateur.
Le fichier original n'a pas ete modifie. Les captures montrent un flux externe
desactive et une seance de verification, pas une preuve de resultat financier.

Le rendu a ete inspecte a 1512 px et 390 px, le Wiki, la recherche, le tutoriel,
les boutons de visite et les confirmations. Pas de debordement horizontal
observe a 390 px dans le tutoriel. Les captures ne contiennent pas le runtime
HTML factice de la maquette.

## Ce qui n'a pas ete teste

- Docker Engine n'est pas disponible ici : image non construite ni executee.
- Aucune comparaison de disponibilite Kraken/Coinbase sur la machine de l'utilisateur.
- Connexions externes non retestees pour cette refonte ; les clients sont inchanges.
- Pas de demonstration de gain predictif ou de profit ; ce n'est pas l'objet de 08.
- Pas de restauration automatique d'une archive de seance via l'interface :
  telechargement pour conservation uniquement. La restauration d'installation
  reste le script qui restaure code et donnees ensemble.

## Reproduire

```
OPENBLAS_NUM_THREADS=1 python -m pytest -q
PYTHONPATH=. python scripts/build_wiki07.py
PYTHONPATH=. python scripts/build_wiki08.py
```

requirements-dev.txt inclut les outils des tests Python et du generateur Wiki.
La fonction verifier du Wiki compare le registre des parametres et l'empreinte
des fichiers du modele. Modifier ces fichiers demande de regenerer les pages.
