# Flytrade alpha 08 - laboratoire guide

Simulation locale, sans argent reel. Interface et parcours simplifies a partir de
la refonte visuelle fournie. Les equations neuronales, la source du marche et les
regles economiques d'alpha 07 sont conservees.

## Demarrer

Installation neuve : `sudo docker compose up --build -d`, puis
http://127.0.0.1:8088. Utiliser /guide pour le parcours pratique.

Installation existante : **ne pas extraire sur le projet actif**. Lire
LIRE-MOI-MISE-A-JOUR.md et lancer le script depuis un dossier distinct. La mise a
jour conserve le volume, .env, les baselines, le corpus et les poids sauvegardes.

## Le point important : deux copies

L'atelier apprend sur les exercices. Le marche utilise une copie gelee. Une
modification de l'atelier ne remplace pas automatiquement la copie du marche.
Le bandeau d'identite montre les deux cerveaux et leurs corrections apprises.

## Les actions

| Bouton | Effet |
|---|---|
| Reprendre ma seance | Continue au prochain exemple, sans reset |
| Rejouer depuis zero | Plan/graine/reglages sauvegardes, cerveau neuf |
| Continuer l'apprentissage | Poids conserves, un passage sur le TRAIN sauvegarde, nouvelle calibration |
| Preparer une nouvelle experience | Nouveau plan selon formulaire/corpus, cerveau neuf |
| Cerveau neuf dans l'atelier | Archive puis reset atelier uniquement ; corpus intact |
| Cerveau neuf sur le marche | Archive puis reset cerveau live ; capital et atelier intacts |
| Portefeuille a 20 EUR | Reset financier uniquement, poids intacts |
| Utiliser ce cerveau sur le marche | Copie poids+calibration ; nouvelle session de 20 EUR, politique arretee |

Continuer n'ajoute pas les nouvelles dates du corpus aux poids existants. Pour
un autre corpus ou d'autres reglages : preparer une nouvelle experience.

## Aide

- Tutoriel integre /guide et visite contextuelle des commandes.
- Wiki /wiki : parcours Debuter, reference technique conservee et sources.
- Guide texte : docs/DEMARRER-08.md.
- Versions de sauvegarde de l'atelier : liste telechargeable en bas de l'atelier.
  Il n'y a pas encore de restauration automatique d'une archive de seance.

## Ce qui n'est pas demontre

Les neurones sont synthetiques. MaleCNS n'est pas importe. Les cotations restent
hypothetiques. Le profit du simulateur n'est pas une preuve de profit Euphoria.
Cette refonte ne promet aucune hausse de precision : elle rend les experiences
plus faciles a comprendre, a recommencer et a auditer.

## Tests locaux

`OPENBLAS_NUM_THREADS=1 python -m pytest -q`

Les tests de scripts utilisent un double de Docker. Voir VALIDATION.md pour les
verifications effectivement realisees et les limites de l'environnement.

Regenerer le Wiki apres modification de code :

```
PYTHONPATH=. python scripts/build_wiki07.py
PYTHONPATH=. python scripts/build_wiki08.py
```

La source est controlee par FLYTRADE_FEED (kraken par defaut, coinbase pour le
profil historique, off pour les tests). Chaque source conserve ses donnees et sa
calibration ; le changement de source n'importe pas implicitement l'ancien modele.
